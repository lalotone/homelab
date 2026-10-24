#!/usr/bin/env python3
"""rocky-cam: one photo per request, for Rocky's eyes (the esp32-homeai voice API's look_through_camera tool).

    GET /snapshot   Authorization: Bearer <token>   -> image/jpeg, taken right now
    GET /health                                     -> "ok" (no camera, no auth)

Privacy by design: there is no stream and nothing is saved; the photo is captured into memory and sent. Only the
addresses in ROCKY_CAM_ALLOW may ask (nobody when unset), and they need the token in ~/.config/rocky-cam/token.
One capture at a time, at most one per MIN_INTERVAL_S. Every request is logged (journalctl --user -u rocky-cam).
Standard library only.
"""
import hmac
import os
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

PORT = int(os.environ.get("ROCKY_CAM_PORT", "8765"))
# Who may ask (comma-separated IPs, from ~/.config/rocky-cam/env). Unset = nobody: fail closed.
ALLOW = {a.strip() for a in os.environ.get("ROCKY_CAM_ALLOW", "").split(",") if a.strip()}
TOKEN_FILE = Path(os.environ.get("ROCKY_CAM_TOKEN_FILE", Path.home() / ".config/rocky-cam/token"))
# Full sensor resolution by default (OV5647: 2592x1944): the API keeps it for zooming into small parts.
WIDTH, HEIGHT = (int(v) for v in os.environ.get("ROCKY_CAM_SIZE", "2592x1944").lower().split("x"))
QUALITY = 92
MIN_INTERVAL_S = 1
CAPTURE_TIMEOUT_S = 15

_lock = threading.Lock()
_last = 0.0


def _token() -> str:
    t = TOKEN_FILE.read_text().strip()
    if len(t) < 32:
        raise RuntimeError("token too short")
    return t


# Exposure is metered here, not by the camera: with this OV5647 module and libcamera 0.7 the auto-exposure acts as
# if the room were dark (30 ms at 3.8x gain in daylight, ~16x too much, half the image pure white, EV ignored).
# So: a tiny test frame at a known exposure, its mean brightness, and the photo at the exposure that hits TARGET_LUMA.
METER_W, METER_H, TARGET_LUMA = 320, 240, 110
MAX_SHUTTER_US, MAX_GAIN = 33000, 8.0
# Metering costs 1-3 test frames (~1 s each), so its result is reused for METER_TTL_S: Live View takes a photo every
# few seconds, and a bench's light doesn't change that fast. A photo then takes ~1.5 s instead of 3-4 s.
METER_TTL_S = 30
_metered: tuple[float, int, float] | None = None   # (when, shutter, gain)


def _luma(shutter_us: int, gain: float) -> float:
    """Mean brightness (0-255) of a small YUV frame: the Y plane comes first, no image library needed."""
    y = subprocess.run(
        ["rpicam-still", "-n", "-t", "300", "--width", str(METER_W), "--height", str(METER_H), "--shutter",
         str(shutter_us), "--gain", f"{gain:.2f}", "-e", "yuv420", "-o", "-"],
        capture_output=True, timeout=CAPTURE_TIMEOUT_S, check=True).stdout[:METER_W * METER_H]
    if len(y) < METER_W * METER_H:
        raise RuntimeError("camera returned no frame")
    return sum(y) / len(y)


def meter() -> tuple[int, float]:
    """Shutter (us) and gain that give a well-exposed photo, in 1-3 test frames."""
    shutter, gain = 4000, 1.0
    for _ in range(3):
        m = _luma(shutter, gain)
        if 80 <= m <= 140:
            break
        want = shutter * gain * TARGET_LUMA / max(m, 1.0) / (4.0 if m > 250 else 1.0)  # saturated: overshoot down
        gain = max(1.0, min(MAX_GAIN, want / MAX_SHUTTER_US))  # shutter first, gain only once it's at its maximum
        shutter = int(max(100, min(MAX_SHUTTER_US, want / gain)))
    return shutter, gain


def capture() -> bytes:
    """A well-exposed JPEG straight from rpicam-still's stdout: it never touches the disk."""
    global _metered
    if _metered is None or time.monotonic() - _metered[0] > METER_TTL_S:
        _metered = (time.monotonic(), *meter())
    _when, shutter, gain = _metered
    out = subprocess.run(
        ["rpicam-still", "-n", "-t", "200", "--width", str(WIDTH), "--height", str(HEIGHT), "-q", str(QUALITY),
         "--shutter", str(shutter), "--gain", f"{gain:.2f}", "-o", "-"],
        capture_output=True, timeout=CAPTURE_TIMEOUT_S, check=True)
    if not out.stdout.startswith(b"\xff\xd8"):
        raise RuntimeError("camera returned no JPEG")
    return out.stdout


class Handler(BaseHTTPRequestHandler):
    server_version = "rocky-cam"
    sys_version = ""

    def log_message(self, fmt, *args):  # one line per request, to the journal
        print(f"{self.client_address[0]} {fmt % args}", flush=True)

    def _reply(self, code: int, body: bytes = b"", ctype: str = "text/plain") -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        global _last
        if self.path == "/health":
            return self._reply(200, b"ok")
        if self.path != "/snapshot":
            return self._reply(404)
        if self.client_address[0] not in ALLOW:
            print(f"refused {self.client_address[0]}: not allowed", flush=True)
            return self._reply(403)
        given = self.headers.get("Authorization", "").removeprefix("Bearer ").strip()
        try:
            ok = hmac.compare_digest(given.encode(), _token().encode())
        except Exception as e:
            print(f"token unavailable: {e}", flush=True)
            return self._reply(500)
        if not ok:
            print(f"refused {self.client_address[0]}: bad token", flush=True)
            return self._reply(401)
        if not _lock.acquire(blocking=False):
            return self._reply(429, b"busy")
        try:
            wait = MIN_INTERVAL_S - (time.monotonic() - _last)
            if wait > 0:
                return self._reply(429, b"too soon")
            t0 = time.monotonic()
            try:
                jpeg = capture()
            except Exception as e:
                print(f"capture failed: {type(e).__name__}", flush=True)
                return self._reply(503, b"camera error")
            _last = time.monotonic()
            print(f"photo taken for {self.client_address[0]}: {len(jpeg) // 1024} KB in {_last - t0:.1f}s", flush=True)
            return self._reply(200, jpeg, "image/jpeg")
        finally:
            _lock.release()

    def do_POST(self):
        self._reply(405)

    do_PUT = do_DELETE = do_PATCH = do_POST


if __name__ == "__main__":
    _token()  # fail fast without a token
    print(f"rocky-cam on :{PORT}, allowed: {', '.join(sorted(ALLOW)) or 'nobody (set ROCKY_CAM_ALLOW)'}", flush=True)
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
