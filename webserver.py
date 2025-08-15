from threading import Thread
from flask import Flask, Response, send_from_directory
import time
import logging
import os
import sys

# Отключение логирования Flask
log = logging.getLogger('werkzeug')
log.setLevel(logging.ERROR)

class WebServer:
    def __init__(self, renderer, host="0.0.0.0", port=6969):
        self.renderer = renderer
        self.host = host
        self.port = port
        self._thread = None
        self.app = Flask("WebPNGTuberStream")
        self.is_running = False
        self.app.config['SEND_FILE_MAX_AGE_DEFAULT'] = 0  # Отключение кэширования
        
        # Определение базовой директории
        if getattr(sys, 'frozen', False):
            self.base_dir = os.path.dirname(sys.executable)
        else:
            self.base_dir = os.path.dirname(os.path.abspath(__file__))

        @self.app.route("/stream")
        def stream():
            return Response(
                self.mjpeg_generator(),
                mimetype="multipart/x-mixed-replace; boundary=frame"
            )

        @self.app.route("/")
        def index():
            return """<html>
<head>
    <title>WebPNGTuber</title>
    <link rel="icon" href="/favicon.ico" type="image/x-icon">
    <style>body { margin: 0; background: #000; }</style>
</head>
<body>
    <img src="/stream" style="width:100vw; height:100vh; object-fit:contain;"/>
</body>
</html>"""

        @self.app.route("/favicon.ico")
        def favicon():
            return send_from_directory(
                self.base_dir,
                'favicon.ico',
                mimetype='image/vnd.microsoft.icon'
            )
                
    def mjpeg_generator(self):
        """Генератор MJPEG потока"""
        while self.is_running:
            frame = self.renderer.get_frame_bytes()
            if frame:
                yield (b"--frame\r\n"
                       b"Content-Type: image/png\r\n"
                       b"Content-Length: " + str(len(frame)).encode() + b"\r\n\r\n" + frame + b"\r\n")
            time.sleep(1.0 / self.renderer.fps)
                
    def start(self):
        """Запуск веб-сервера"""
        if self.is_running:
            return
        def run():
            self.is_running = True
            try:
                self.app.run(
                    host=self.host, 
                    port=self.port, 
                    threaded=True, 
                    debug=False, 
                    use_reloader=False
                )
            finally:
                self.is_running = False
                
        self._thread = Thread(target=run, daemon=True)
        self._thread.start()

    def stop(self):
        """Остановка веб-сервера"""
        self.is_running = False