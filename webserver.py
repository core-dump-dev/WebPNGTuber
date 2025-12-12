from threading import Thread
from flask import Flask, Response, send_from_directory
import time
import logging
import os
import sys
import logging.handlers
from datetime import datetime

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
    logger.setLevel(logging.INFO)  # Уменьшили уровень логирования
    
    formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    
    log_file = os.path.join(LOGS_DIR, 'webserver.log')
    file_handler = logging.handlers.RotatingFileHandler(
        log_file, maxBytes=1048576, backupCount=5
    )
    file_handler.setFormatter(formatter)
    
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    
    logger.addHandler(file_handler)
    logger.addHandler(console_handler)
    
    return logger

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
        self.app.config['SEND_FILE_MAX_AGE_DEFAULT'] = 0
        
        # Определение базовой директории
        if getattr(sys, 'frozen', False):
            self.base_dir = os.path.dirname(sys.executable)
        else:
            self.base_dir = os.path.dirname(os.path.abspath(__file__))
        
        # Оптимизация: кэширование статичных ответов
        self._index_cache = None
        self._favicon_cache = None
        
        @self.app.route("/stream")
        def stream():
            logger.debug("Stream connection established")
            return Response(
                self._optimized_mjpeg_generator(),
                mimetype="multipart/x-mixed-replace; boundary=frame"
            )
        
        @self.app.route("/")
        def index():
            logger.debug("Index page requested")
            if self._index_cache is None:
                self._index_cache = """<html>
<head>
    <title>WebPNGTuber</title>
    <link rel="icon" href="/favicon.ico" type="image/x-icon">
    <style>body { margin: 0; background: #000; }</style>
</head>
<body>
    <img src="/stream" style="width:100vw; height:100vh; object-fit:contain;"/>
</body>
</html>"""
            return self._index_cache
        
        @self.app.route("/favicon.ico")
        def favicon():
            if self._favicon_cache is None:
                try:
                    favicon_path = os.path.join(self.base_dir, 'favicon.ico')
                    if os.path.exists(favicon_path):
                        self._favicon_cache = send_from_directory(
                            self.base_dir,
                            'favicon.ico',
                            mimetype='image/vnd.microsoft.icon'
                        )
                    else:
                        # Создаем пустой ответ если иконки нет
                        self._favicon_cache = ('', 404)
                except Exception as e:
                    logger.error(f"Error loading favicon: {e}")
                    self._favicon_cache = ('', 404)
            
            return self._favicon_cache
    
    def _optimized_mjpeg_generator(self):
        """Оптимизированный генератор MJPEG потока"""
        frame_count = 0
        last_stats_time = time.time()
        
        while self.is_running:
            try:
                frame_start = time.time()
                frame = self.renderer.get_frame_bytes()
                
                if frame:
                    # Минимизируем аллокации строк
                    frame_len = str(len(frame))
                    yield (b"--frame\r\n"
                           b"Content-Type: image/png\r\n"
                           b"Content-Length: " + frame_len.encode() + b"\r\n\r\n" + frame + b"\r\n")
                
                frame_count += 1
                
                # Логирование статистики
                current_time = time.time()
                if current_time - last_stats_time >= 5.0:  # Каждые 5 секунд
                    fps = frame_count / 5.0
                    logger.debug(f"Stream FPS: {fps:.1f}")
                    frame_count = 0
                    last_stats_time = current_time
                
                # Оптимизированная задержка
                elapsed = time.time() - frame_start
                target_delay = 1.0 / self.renderer.fps
                sleep_time = target_delay - elapsed
                
                if sleep_time > 0:
                    time.sleep(min(sleep_time, target_delay))
                elif elapsed > target_delay * 2:
                    logger.warning(f"Frame generation took too long: {elapsed*1000:.1f}ms")
                    
            except Exception as e:
                logger.error(f"Error in stream generator: {e}")
                time.sleep(0.016)  # Спим на 1 кадр при ошибке
    
    def start(self):
        """Запуск веб-сервера"""
        if self.is_running:
            return
        
        def run():
            self.is_running = True
            try:
                logger.info(f"Web server starting on {self.host}:{self.port}")
                
                # Оптимизированные настройки Flask
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