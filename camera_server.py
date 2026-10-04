"""Optional Logi/USB camera service. Run separately from the audio server.

    CAMERA_DEVICE=/dev/video0 /usr/bin/python3 camera_server.py

One camera capture is shared by all viewers; no video is written to disk.
Also serves the web app (../frontend) at http://<pi>:8081/ so the page and camera come from the
same plain-HTTP address (browsers that auto-upgrade a separate camera URL to HTTPS broke the feed).
"""
import os
import threading
import time

from flask import Flask, Response, jsonify, send_from_directory

FRONTEND = os.path.join(os.path.dirname(os.path.abspath(__file__)), "frontend")


class Camera:
    def __init__(self, device, fps=10):
        self.device = device
        self.fps = max(1, min(30, fps))
        self.condition = threading.Condition()
        self.thread = None
        self.frame = None
        self.sequence = 0
        self.error = "Camera has not been opened"
        self.last_frame = 0.0
        self.viewers = 0
        self.stopping = threading.Event()

    def stop(self):
        """Stop capturing and release the camera (Ctrl+C killed it mid-read: 'FATAL: exception not rethrown')."""
        self.stopping.set()
        if self.thread is not None:
            self.thread.join(timeout=3)

    def start(self):
        with self.condition:
            if self.thread is None:
                self.thread = threading.Thread(target=self._capture, daemon=True)
                self.thread.start()

    def _capture(self):
        # Optional system package; importing this module does not need OpenCV.
        try:
            import cv2
        except ImportError:
            with self.condition:
                self.error = "OpenCV missing: install python3-opencv and run with /usr/bin/python3"
                self.condition.notify_all()
            return
        while not self.stopping.is_set():
            capture = None
            try:
                capture = cv2.VideoCapture(self.device, cv2.CAP_V4L2)
                if not capture.isOpened():
                    raise RuntimeError(f"Cannot open {self.device}; check device, permissions, and other camera apps")
                capture.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
                capture.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
                capture.set(cv2.CAP_PROP_FPS, self.fps)
                while not self.stopping.is_set():
                    tick = time.monotonic()
                    ok, frame = capture.read()
                    if not ok:
                        raise RuntimeError("No camera frame received; check the USB connection")
                    ok, jpeg = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, 75])
                    if not ok:
                        raise RuntimeError("JPEG encoding failed")
                    with self.condition:
                        self.frame = jpeg.tobytes()
                        self.sequence += 1
                        self.last_frame = time.monotonic()
                        self.error = None
                        self.condition.notify_all()
                    time.sleep(max(0, 1 / self.fps - (time.monotonic() - tick)))
            except Exception as exc:
                with self.condition:
                    self.frame = None
                    self.error = str(exc)
                    self.condition.notify_all()
            finally:
                if capture is not None:
                    capture.release()
            self.stopping.wait(2)

    def ready(self, timeout=5):
        self.start()
        with self.condition:
            self.condition.wait_for(lambda: self.frame is not None, timeout=timeout)
            return self.frame is not None and time.monotonic() - self.last_frame < 5

    def frames(self):
        sequence, stalled = -1, 0.0
        with self.condition:
            self.viewers += 1
        try:
            while True:
                with self.condition:
                    available = self.condition.wait_for(
                        lambda: self.sequence != sequence and self.frame is not None, timeout=5)
                    if not available:
                        # Wait out short stalls (_capture reopens the camera): a browser <img> doesn't
                        # notice an MJPEG stream ending, so closing it would freeze the page for good.
                        stalled += 5
                        if stalled >= 60:
                            return
                        continue
                    stalled = 0.0
                    sequence, frame = self.sequence, self.frame
                yield (b'--frame\r\nContent-Type: image/jpeg\r\nContent-Length: '
                       + str(len(frame)).encode() + b'\r\n\r\n' + frame + b'\r\n')
        finally:
            with self.condition:
                self.viewers -= 1


def create_app(camera=None):
    # Flask's built-in /static route would otherwise point at ./static (missing) and 404 the CSS/JS.
    app = Flask(__name__, static_folder=os.path.join(FRONTEND, "static"), static_url_path="/static")
    camera = camera or Camera(os.environ.get('CAMERA_DEVICE', '/dev/video0'))
    app.config['camera'] = camera
    camera.start()  # open at boot so /camera/status reports the real state, not "not opened"

    @app.get('/')
    def web_app():
        return send_from_directory(FRONTEND, 'index.html')

    @app.get('/camera/status')
    def status():
        camera.start()
        with camera.condition:
            return jsonify(device=camera.device,
                           ready=camera.frame is not None and time.monotonic() - camera.last_frame < 5,
                           error=camera.error, viewers=camera.viewers)

    @app.get('/camera/stream')
    def stream():
        if not camera.ready():
            return jsonify(error=camera.error or 'Camera timed out'), 503
        return Response(camera.frames(), mimetype='multipart/x-mixed-replace; boundary=frame',
                        headers={'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff'})

    return app


if __name__ == '__main__':
    app = create_app()
    try:
        app.run(host='0.0.0.0', port=int(os.environ.get('CAMERA_PORT', '8081')),
                threaded=True, debug=False, use_reloader=False)
    finally:
        app.config['camera'].stop()
