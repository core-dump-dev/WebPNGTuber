from threading import Thread
from flask import Flask, Response, stream_with_context
import time

def mjpeg_response(renderer):
    boundary = "--frameboundary"
    def gen():
        while True:
            frame = renderer.get_frame_bytes()
            if not frame:
                time.sleep(0.01)
                continue
            yield (b"--frameboundary\r\n"
                   b"Content-Type: image/png\r\n"
                   b"Content-Length: " + str(len(frame)).encode() + b"\r\n\r\n" + frame + b"\r\n")
            time.sleep(1.0 / renderer.fps)
    return gen

class WebServer:
    def __init__(self, renderer, host="0.0.0.0", port=6969):
        self.renderer = renderer
        self.host = host
        self.port = port
        self._thread = None
        self.app = Flask("SimplePNGTuberStream")
        self.is_running = False

        @self.app.route("/stream")
        def stream():
            return Response(mjpeg_response(self.renderer)(),
                            mimetype="multipart/x-mixed-replace; boundary=--frameboundary")

        @self.app.route("/")
        def index():
            return "<html><body><img src='/stream' /></body></html>"

    def start(self):
        if self.is_running:
            return
        def run():
            self.is_running = True
            try:
                self.app.run(host=self.host, port=self.port, threaded=True, debug=False)
            finally:
                self.is_running = False
        self._thread = Thread(target=run, daemon=True)
        self._thread.start()

    def stop(self):
        # stopping Flask dev server programmatically is non-trivial; we rely on process exit.
        self.is_running = False