/**
 * App.jsx — Real-Time Face Detection Frontend
 *
 * Data flow:
 *  1. Webcam → hidden <video> → hidden <canvas> → JPEG base64
 *  2. JPEG frames sent via WebSocket  →  WS /feed/ingest
 *  3. Backend annotates frames, pushes to MJPEG broker
 *  4. <img src="/feed/stream"> displays the MJPEG stream natively
 *  5. Frontend polls GET /api/roi for stored ROI history
 */

import { useCallback, useEffect, useRef, useState } from "react";

// ─── Config ────────────────────────────────────────────────────────────────
// VITE_BACKEND_URL is injected at build time by Render (or empty for local dev)
const BACKEND_URL = import.meta.env.VITE_BACKEND_URL || "";
const BACKEND_WS  = BACKEND_URL.replace(/^http/, "ws"); // http→ws, https→wss

const WS_INGEST   = BACKEND_WS  ? `${BACKEND_WS}/feed/ingest`  : `ws://${window.location.host}/feed/ingest`;
const STREAM_URL  = BACKEND_URL ? `${BACKEND_URL}/feed/stream`  : `http://localhost:8000/feed/stream`;
const API_BASE    = BACKEND_URL || "http://localhost:8000";


const CAPTURE_FPS = 10;
const CAPTURE_INTERVAL = Math.floor(1000 / CAPTURE_FPS);
const JPEG_QUALITY = 0.72;
const ROI_POLL_MS = 2500;

// ─── Helpers ────────────────────────────────────────────────────────────────
function relTime(isoStr) {
  if (!isoStr) return "";
  const diff = Math.floor((Date.now() - new Date(isoStr)) / 1000);
  if (diff < 5) return "just now";
  if (diff < 60) return `${diff}s ago`;
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
  return new Date(isoStr).toLocaleTimeString();
}

// ─── Toast manager ──────────────────────────────────────────────────────────
function useToasts() {
  const [toasts, setToasts] = useState([]);
  const push = useCallback((msg, type = "info") => {
    const id = Date.now() + Math.random();
    setToasts((p) => [...p.slice(-4), { id, msg, type }]);
    setTimeout(() => setToasts((p) => p.filter((t) => t.id !== id)), 3200);
  }, []);
  return { toasts, push };
}

// ═══════════════════════════════════════════════════════════════════════════
export default function App() {
  const videoRef  = useRef(null);   // Hidden webcam <video>
  const canvasRef = useRef(null);   // Hidden capture <canvas>
  const wsRef     = useRef(null);   // WebSocket to /feed/ingest
  const timerRef  = useRef(null);   // setInterval handle
  const streamRef = useRef(null);   // MediaStream

  const [streaming, setStreaming]   = useState(false);
  const [wsStatus, setWsStatus]     = useState("idle");
  const [fps, setFps]               = useState(0);
  const [framesSent, setFramesSent] = useState(0);
  const [storedRois, setStoredRois] = useState([]);
  const [latestRoi, setLatestRoi]   = useState(null);
  const [roiCount, setRoiCount]     = useState(0);
  const [streamKey, setStreamKey]   = useState(0); // re-mount img on restart

  const fpsRef = useRef({ n: 0, ts: Date.now() });
  const { toasts, push } = useToasts();

  // ── Fetch ROI history ────────────────────────────────────────────────────
  const fetchRois = useCallback(async () => {
    try {
      const [listRes, latestRes] = await Promise.all([
        fetch(`${API_BASE}/api/roi?limit=25`),
        fetch(`${API_BASE}/api/roi/latest`),
      ]);
      if (listRes.ok) {
        const d = await listRes.json();
        setStoredRois(d.data ?? []);
        setRoiCount(d.count ?? 0);
      }
      if (latestRes.ok) {
        const d = await latestRes.json();
        setLatestRoi(d.data ?? null);
      }
    } catch { /* backend not up yet */ }
  }, []);

  useEffect(() => {
    fetchRois();
    const t = setInterval(fetchRois, ROI_POLL_MS);
    return () => clearInterval(t);
  }, [fetchRois]);

  // ── Start streaming ──────────────────────────────────────────────────────
  const startStream = useCallback(async () => {
    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        video: { width: { ideal: 640 }, height: { ideal: 480 } },
        audio: false,
      });
      streamRef.current = stream;
      if (videoRef.current) {
        videoRef.current.srcObject = stream;
        await videoRef.current.play();
      }

      setWsStatus("connecting");
      const ws = new WebSocket(WS_INGEST);
      wsRef.current = ws;

      ws.onopen = () => {
        setWsStatus("connected");
        push("WebSocket connected ✓", "success");

        timerRef.current = setInterval(() => {
          const vid = videoRef.current;
          const cvs = canvasRef.current;
          if (!vid || !cvs || ws.readyState !== WebSocket.OPEN) return;

          const w = vid.videoWidth || 640;
          const h = vid.videoHeight || 480;
          if (cvs.width !== w) cvs.width = w;
          if (cvs.height !== h) cvs.height = h;

          cvs.getContext("2d").drawImage(vid, 0, 0, w, h);
          ws.send(cvs.toDataURL("image/jpeg", JPEG_QUALITY));
          setFramesSent((n) => n + 1);

          // FPS
          const fc = fpsRef.current;
          fc.n++;
          const now = Date.now();
          if (now - fc.ts >= 1000) {
            setFps(Math.round((fc.n * 1000) / (now - fc.ts)));
            fc.n = 0;
            fc.ts = now;
          }
        }, CAPTURE_INTERVAL);
      };

      ws.onerror = () => { setWsStatus("error"); push("WebSocket error!", "error"); };
      ws.onclose = () => { setWsStatus("idle"); setStreaming(false); };

      setStreaming(true);
      setStreamKey((k) => k + 1); // re-mount MJPEG img
    } catch (err) {
      push(`Camera error: ${err.message}`, "error");
    }
  }, [push]);

  // ── Stop streaming ───────────────────────────────────────────────────────
  const stopStream = useCallback(() => {
    clearInterval(timerRef.current);
    wsRef.current?.close();
    streamRef.current?.getTracks().forEach((t) => t.stop());
    setStreaming(false);
    setWsStatus("idle");
    setFps(0);
    push("Stream stopped.", "info");
  }, [push]);

  useEffect(() => () => {
    clearInterval(timerRef.current);
    wsRef.current?.close();
    streamRef.current?.getTracks().forEach((t) => t.stop());
  }, []);

  const statusClass =
    wsStatus === "connected" ? "connected"
    : wsStatus === "error"   ? "error"
    : "";

  const statusLabel =
    wsStatus === "connected" ? "Live"
    : wsStatus === "connecting" ? "Connecting…"
    : wsStatus === "error" ? "Error"
    : "Offline";

  // ─── Render ──────────────────────────────────────────────────────────────
  return (
    <>
      <div className="bg-mesh" aria-hidden="true" />
      <video ref={videoRef} style={{ display: "none" }} muted playsInline />
      <canvas ref={canvasRef} style={{ display: "none" }} />

      <div className="app-root">
        {/* ── Header ── */}
        <header className="header">
          <div className="header-brand">
            <div className="brand-icon">🎯</div>
            <div>
              <div className="brand-name">FaceDetect</div>
              <div className="brand-tag">Real-Time Vision System</div>
            </div>
          </div>
          <div className="header-status">
            <span className={`status-dot ${statusClass}`} />
            {statusLabel}
            {streaming && (
              <span className="fps-pill">
                {fps} <span className="fps-unit">fps</span>
              </span>
            )}
          </div>
        </header>

        {/* ── Main ── */}
        <main className="main-grid">

          {/* ─ Video panel ─ */}
          <section className="panel video-panel">
            <div className="panel-header">
              <h2 className="panel-title">
                <span>📹</span> Live Feed
                <span className="endpoint-pill">GET /feed/stream</span>
              </h2>
              {streaming && <span className="badge-live">● LIVE</span>}
            </div>

            <div className="video-wrapper">
              {/* Native MJPEG via <img> — no canvas needed on receive side */}
              <img
                key={streamKey}
                src={streaming ? `${STREAM_URL}?t=${streamKey}` : undefined}
                alt="Processed video feed with face detection bounding box"
                className={`stream-img ${streaming ? "visible" : "hidden"}`}
              />

              {!streaming && (
                <div className="video-placeholder">
                  <div className="placeholder-icon">📷</div>
                  <p className="placeholder-text">Press Start to begin detection</p>
                  <div className="endpoint-chain">
                    <code>WS /feed/ingest</code>
                    <span>→ dlib → Pillow →</span>
                    <code>GET /feed/stream</code>
                  </div>
                </div>
              )}

              <div className="scan-lines" aria-hidden="true" />
              {streaming && <div className="scan-bar" aria-hidden="true" />}

              {streaming && latestRoi && (
                <div className="face-chip">
                  <span className="face-chip-dot" />
                  Face detected
                </div>
              )}
            </div>

            {/* Controls */}
            <div className="controls-bar">
              {!streaming ? (
                <button id="btn-start" className="btn btn-primary" onClick={startStream}
                  disabled={wsStatus === "connecting"}>
                  {wsStatus === "connecting"
                    ? <><span className="spinner" /> Connecting…</>
                    : <>▶ Start Stream</>}
                </button>
              ) : (
                <button id="btn-stop" className="btn btn-danger" onClick={stopStream}>
                  ⏹ Stop
                </button>
              )}

              <button id="btn-refresh" className="btn btn-ghost" onClick={fetchRois}
                title="Refresh ROI database records">
                🔄 Refresh ROI
              </button>

              <div className="stats-inline">
                <span className="stat-chip">
                  <span className="stat-chip-label">Sent</span>
                  <span className="stat-chip-val">{framesSent}</span>
                </span>
                <span className="stat-chip">
                  <span className="stat-chip-label">Saved</span>
                  <span className="stat-chip-val">{roiCount}</span>
                </span>
              </div>
            </div>
          </section>

          {/* ─ Sidebar ─ */}
          <aside className="sidebar">

            {/* Latest ROI box */}
            <div className="panel">
              <div className="panel-header">
                <h2 className="panel-title">
                  <span>🔲</span> Current ROI
                  <span className="endpoint-pill">GET /api/roi/latest</span>
                </h2>
              </div>
              {latestRoi ? (
                <div className="roi-detail">
                  <p className="roi-detail-label">
                    Axis-aligned minimal bounding box (pixels)
                  </p>
                  <div className="bbox-grid">
                    {["top","right","bottom","left"].map((k) => (
                      <div className="bbox-cell" key={k}>
                        <div className="bbox-cell-label">{k}</div>
                        <div className="bbox-cell-val">
                          {latestRoi.bounding_box?.[k] ?? "—"}
                        </div>
                      </div>
                    ))}
                  </div>
                  <div className="bbox-meta">
                    <div className="meta-chip">
                      <span>W</span>
                      <strong>{latestRoi.dimensions?.width ?? "—"}</strong>
                    </div>
                    <div className="meta-chip">
                      <span>H</span>
                      <strong>{latestRoi.dimensions?.height ?? "—"}</strong>
                    </div>
                    <div className="meta-chip">
                      <span>Area</span>
                      <strong>{latestRoi.dimensions?.area?.toLocaleString() ?? "—"}</strong>
                    </div>
                    <div className="meta-chip">
                      <span>cx</span>
                      <strong>{latestRoi.dimensions?.center_x ?? "—"}</strong>
                    </div>
                    <div className="meta-chip">
                      <span>cy</span>
                      <strong>{latestRoi.dimensions?.center_y ?? "—"}</strong>
                    </div>
                  </div>
                </div>
              ) : (
                <div className="roi-empty">
                  <span>🔍</span>
                  <p>No face detected yet</p>
                </div>
              )}
            </div>

            {/* ROI history */}
            <div className="panel panel-grow">
              <div className="panel-header">
                <h2 className="panel-title">
                  <span>🗄️</span> ROI History
                  <span className="endpoint-pill">GET /api/roi</span>
                </h2>
                <span className="badge-count">{storedRois.length}</span>
              </div>
              <div className="roi-list-scroll">
                {storedRois.length === 0 ? (
                  <div className="roi-empty">
                    <span>📭</span>
                    <p>No records yet</p>
                    <p className="text-muted">Start the stream to populate</p>
                  </div>
                ) : (
                  <ul>
                    {storedRois.map((roi) => (
                      <li key={roi.id} className="roi-row">
                        <div className="roi-row-left">
                          <span className="roi-id"># {roi.id}</span>
                          <span className="roi-coords">
                            T:{roi.bounding_box?.top} R:{roi.bounding_box?.right}{" "}
                            B:{roi.bounding_box?.bottom} L:{roi.bounding_box?.left}
                          </span>
                          <span className="roi-dims">
                            {roi.dimensions?.width} × {roi.dimensions?.height}px
                          </span>
                        </div>
                        <div className="roi-row-right">
                          <span className="roi-time">{relTime(roi.detected_at)}</span>
                        </div>
                      </li>
                    ))}
                  </ul>
                )}
              </div>
            </div>

          </aside>
        </main>

        {/* ── Footer ── */}
        <footer className="footer">
          <span>FastAPI</span> · <span>face_recognition (dlib HOG)</span> · <span>Pillow</span> ·{" "}
          <span>PostgreSQL</span> · <span>React + Vite</span> · <span>MJPEG stream</span> —{" "}
          <em>Zero OpenCV</em>
        </footer>
      </div>

      {/* ── Toasts ── */}
      <div className="toast-container" aria-live="polite">
        {toasts.map((t) => (
          <div key={t.id} className={`toast ${t.type}`}>{t.msg}</div>
        ))}
      </div>
    </>
  );
}
