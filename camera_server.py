"""Optional Logi/USB camera service. Run separately from the audio server.

    CAMERA_DEVICE=/dev/video0 /usr/bin/python3 camera_server.py

One camera capture is shared by all viewers; no video is written to disk.
"""
import os
import threading
import time

from flask import Flask, Response, jsonify


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
        while True:
            capture = None
            try:
                capture = cv2.VideoCapture(self.device, cv2.CAP_V4L2)
                if not capture.isOpened():
                    raise RuntimeError(f"Cannot open {self.device}; check device, permissions, and other camera apps")
                capture.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
                capture.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
                capture.set(cv2.CAP_PROP_FPS, self.fps)
                while True:
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
            time.sleep(2)

    def ready(self, timeout=5):
        self.start()
        with self.condition:
            self.condition.wait_for(lambda: self.frame is not None, timeout=timeout)
            return self.frame is not None and time.monotonic() - self.last_frame < 5

    def frames(self):
        sequence = -1
        with self.condition:
            self.viewers += 1
        try:
            while True:
                with self.condition:
                    available = self.condition.wait_for(
                        lambda: self.sequence != sequence and self.frame is not None, timeout=5)
                    if not available:
                        return  # Close stalled feeds instead of displaying repeated stale frames.
                    sequence, frame = self.sequence, self.frame
                yield (b'--frame\r\nContent-Type: image/jpeg\r\nContent-Length: '
                       + str(len(frame)).encode() + b'\r\n\r\n' + frame + b'\r\n')
        finally:
            with self.condition:
                self.viewers -= 1


def create_app(camera=None):
    app = Flask(__name__)
    camera = camera or Camera(os.environ.get('CAMERA_DEVICE', '/dev/video0'))

    @app.get('/camera/status')
    def status():
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
    create_app().run(host='0.0.0.0', port=int(os.environ.get('CAMERA_PORT', '8081')),
                     threaded=True, debug=False, use_reloader=False)
