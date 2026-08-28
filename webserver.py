# webserver.py
import logging
from threading import Thread
from werkzeug.serving import make_server
from flask import Flask, Response, send_from_directory
import time
import os
import sys
from locale_loader import tr

# Определение базовой директории
if getattr(sys, 'frozen', False):
    BASE_DIR = os.path.dirname(sys.executable)
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# Импортируем логирование из utils
from utils import setup_logging
logger = setup_logging('webserver')

# Отключение логирования Flask
log = logging.getLogger('werkzeug')
log.setLevel(logging.ERROR)


class WebServer:
    def __init__(self, renderer, host="0.0.0.0", port=6969):
        self.renderer = renderer
        self.host = host
        self.port = port
        self._thread = None
        self._server = None
        self.is_running = False

        if getattr(sys, 'frozen', False):
            self.base_dir = os.path.dirname(sys.executable)
        else:
            self.base_dir = os.path.dirname(os.path.abspath(__file__))

        self.app = Flask("WebPNGTuberStream")

        self.app.config['SEND_FILE_MAX_AGE_DEFAULT'] = 0
        self.app.config['JSONIFY_PRETTYPRINT_REGULAR'] = False

        @self.app.route("/stream")
        def stream():
            logger.info(tr('web_stream_connected', port=self.port))
            return Response(
                self.mjpeg_generator(),
                mimetype="multipart/x-mixed-replace; boundary=frame"
            )

        @self.app.route("/")
        def index():
            logger.info(tr('web_index_requested', port=self.port))
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
        """Оптимизированный генератор MJPEG потока с прямым сравнением байтов вместо хеша"""
        last_frame_data = None
        last_frame_bytes = None

        while self.is_running:
            if not self.renderer or not self.renderer.model:
                time.sleep(0.1)
                continue

            frame = self.renderer.get_frame_bytes()
            if frame:
                # Сравниваем байты напрямую (быстрее, чем вычисление хеша)
                if last_frame_bytes is not None and frame == last_frame_bytes:
                    # Кадр не изменился – отправляем сохранённый пакет
                    if last_frame_data is not None:
                        yield last_frame_data
                else:
                    frame_data = (
                        b"--frame\r\n"
                        b"Content-Type: image/png\r\n"
                        b"Content-Length: " + str(len(frame)).encode() + b"\r\n\r\n" +
                        frame + b"\r\n"
                    )
                    last_frame_data = frame_data
                    last_frame_bytes = frame
                    yield frame_data

            time.sleep(1.0 / self.renderer.fps)

    def start(self):
        """Запуск веб-сервера"""
        if self.is_running:
            return

        def run():
            try:
                self._server = make_server(
                    self.host,
                    self.port,
                    self.app,
                    threaded=True
                )
                self.is_running = True
                logger.info(tr('web_server_starting', host=self.host, port=self.port))
                self._server.serve_forever()
            except Exception as e:
                logger.error(tr('web_server_error', port=self.port, error=e))
                self.is_running = False
            finally:
                if self._server:
                    self._server.server_close()
                logger.info(tr('web_server_stopped'))

        self._thread = Thread(target=run, daemon=True)
        self._thread.start()

        time.sleep(0.5)

    def stop(self):
        """Остановка веб-сервера"""
        if not self.is_running or not self._server:
            return

        logger.info(f"Web server stop requested for port {self.port}")
        self.is_running = False

        try:
            if self._server:
                self._server.shutdown()
                time.sleep(0.5)
        except Exception as e:
            logger.error(f"Error stopping server: {e}")