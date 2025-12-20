from threading import Thread
from flask import Flask, Response, send_from_directory
import time
import logging
import os
import sys
import logging.handlers
from datetime import datetime
import threading

# Определение базовой директории
if getattr(sys, 'frozen', False):
    BASE_DIR = os.path.dirname(sys.executable)
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# Создание папки для логов
LOGS_DIR = os.path.join(BASE_DIR, "logs")
os.makedirs(LOGS_DIR, exist_ok=True)

# Настройка логирования для webserver
def setup_webserver_logging():
    logger = logging.getLogger('webserver')
    logger.setLevel(logging.DEBUG)
    
    # Форматирование
    formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    
    # Файловый обработчик с ротацией
    log_file = os.path.join(LOGS_DIR, 'webserver.log')
    file_handler = logging.handlers.RotatingFileHandler(
        log_file, maxBytes=1048576, backupCount=5  # 1MB
    )
    file_handler.setFormatter(formatter)
    
    # Консольный обработчик
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    
    logger.addHandler(file_handler)
    logger.addHandler(console_handler)
    
    return logger

# Инициализация логгера
logger = setup_webserver_logging()

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
        
        # Оптимизации Flask
        self.app.config['SEND_FILE_MAX_AGE_DEFAULT'] = 0  # Отключение кэширования
        self.app.config['JSONIFY_PRETTYPRINT_REGULAR'] = False
        
        # Кэш для сжатых кадров
        self._frame_cache = None
        self._frame_cache_time = 0
        self._cache_lock = threading.Lock()
        
        # Определение базовой директории
        if getattr(sys, 'frozen', False):
            self.base_dir = os.path.dirname(sys.executable)
        else:
            self.base_dir = os.path.dirname(os.path.abspath(__file__))

        @self.app.route("/stream")
        def stream():
            logger.info("Stream connection established")
            return Response(
                self.mjpeg_generator(),
                mimetype="multipart/x-mixed-replace; boundary=frame"
            )

        @self.app.route("/")
        def index():
            logger.info("Index page requested")
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
        """Оптимизированный генератор MJPEG потока"""
        last_frame = None
        last_frame_hash = None
        
        while self.is_running:
            frame = self.renderer.get_frame_bytes()
            if frame:
                # Простая дедупликация кадров
                frame_hash = hash(frame)
                if frame_hash == last_frame_hash and last_frame:
                    yield last_frame
                else:
                    # Минимальная обработка
                    frame_data = (
                        b"--frame\r\n"
                        b"Content-Type: image/png\r\n"
                        b"Content-Length: " + str(len(frame)).encode() + b"\r\n\r\n" + 
                        frame + b"\r\n"
                    )
                    last_frame = frame_data
                    last_frame_hash = frame_hash
                    yield frame_data
            
            time.sleep(1.0 / self.renderer.fps)
                
    def start(self):
        """Запуск веб-сервера"""
        if self.is_running:
            return
        def run():
            self.is_running = True
            try:
                logger.info(f"Web server starting on {self.host}:{self.port}")
                self.app.run(
                    host=self.host, 
                    port=self.port, 
                    threaded=True, 
                    debug=False, 
                    use_reloader=False
                )
            except Exception as e:
                logger.error(f"Web server error: {e}")
            finally:
                self.is_running = False
                logger.info("Web server stopped")
                
        self._thread = Thread(target=run, daemon=True)
        self._thread.start()

    def stop(self):
        """Остановка веб-сервера"""
        self.is_running = False
        logger.info("Web server stop requested")