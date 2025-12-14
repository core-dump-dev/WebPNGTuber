import threading, time
import numpy as np
import sounddevice as sd
import sys
import os
import logging
import logging.handlers
from datetime import datetime
from collections import deque

# Определение базовой директории
if getattr(sys, 'frozen', False):
    BASE_DIR = os.path.dirname(sys.executable)
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# Создание папки для логов
LOGS_DIR = os.path.join(BASE_DIR, "logs")
os.makedirs(LOGS_DIR, exist_ok=True)

# Настройка логирования для audio
def setup_audio_logging():
    logger = logging.getLogger('audio')
    logger.setLevel(logging.DEBUG)
    
    # Форматирование
    formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    
    # Файловый обработчик с ротацией
    log_file = os.path.join(LOGS_DIR, 'audio.log')
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
logger = setup_audio_logging()

class AudioProcessor:
    def __init__(self, callback=None, device=None):
        self.callback = callback
        self.running = False
        self._level = 0.0
        self._thread = None
        self.device = device
        self.noise_gate_threshold = 0.01
        self.device_index = None
        self.sensitivity = 1.0
        
        # Сглаживание уровня для уменьшения дрожания
        self._smoothing_buffer = deque(maxlen=3)  # Буфер для сглаживания
        self._smoothing_alpha = 0.7  # Коэффициент сглаживания (0-1, чем больше, тем быстрее реакция)
        self._last_smoothed_level = 0.0

        # Подавление вывода ошибок для EXE
        if getattr(sys, 'frozen', False):
            sys.stderr = open(os.devnull, 'w')

        # Получение индекса устройства по имени
        if device and device != "По умолчанию":
            devices = sd.query_devices()
            for i, dev in enumerate(devices):
                if dev['name'] == device and dev['max_input_channels'] > 0:
                    self.device_index = i
                    break
        
        logger.info(f"Audio processor initialized with device: {device}, samplerate: 22050")

    def set_sensitivity(self, sensitivity):
        """Установка чувствительности"""
        self.sensitivity = max(0.1, min(5.0, sensitivity))
        logger.info(f"Sensitivity set to: {sensitivity}")

    def start(self):
        """Запуск обработки аудио"""
        if self.running:
            return
        self.running = True
        self._thread = threading.Thread(target=self._capture_loop, daemon=True)
        self._thread.start()
        logger.info("Audio processor started")

    def stop(self):
        """Остановка обработки аудио"""
        self.running = False
        if self._thread:
            self._thread.join(timeout=1.0)
        self._thread = None

        # Восстановление stderr для EXE
        if getattr(sys, 'frozen', False):
            sys.stderr = sys.__stderr__
        
        logger.info("Audio processor stopped")

    def _simulate_loop(self):
        """Симуляция аудио (для тестирования)"""
        t = 0.0
        while self.running:
            t += 0.1
            # Более реалистичная симуляция с разными уровнями
            if int(t) % 10 < 3:
                level = 0.1  # Тишина
            elif int(t) % 10 < 6:
                level = 0.4  # Шёпот
            elif int(t) % 10 < 8:
                level = 0.7  # Норма
            else:
                level = 0.9  # Крик
                
            level = level * self.sensitivity
            self._level = level
            if self.callback:
                try:
                    self.callback(level)
                except Exception as e:
                    logger.error(f"Error in callback: {e}")
            time.sleep(0.1)

    def _smooth_level(self, raw_level):
        """Сглаживание уровня аудио для уменьшения дрожания"""
        # Добавляем новое значение в буфер
        self._smoothing_buffer.append(raw_level)
        
        # Экспоненциальное сглаживание для быстрой реакции
        if len(self._smoothing_buffer) > 0:
            # Используем экспоненциальное сглаживание
            current = raw_level
            smoothed = self._smoothing_alpha * current + (1 - self._smoothing_alpha) * self._last_smoothed_level
            self._last_smoothed_level = smoothed
            return smoothed
        return raw_level

    def _capture_loop(self):
        """Основной цикл захвата аудио с оптимизацией"""
        import queue
        q = queue.Queue()
        
        def callback(indata, frames, time_info, status):
            """Аудио callback функция"""
            if not self.running:
                return
            q.put(indata.copy())
        
        try:
            device_params = {}
            if self.device_index is not None:
                device_params['device'] = self.device_index
                
            # Уменьшенная частота дискретизации до 22050 Гц для лучшей производительности
            with sd.InputStream(
                channels=1, 
                callback=callback, 
                samplerate=22050,  # Уменьшено с 44100 до 22050
                blocksize=512,  # Уменьшен размер блока для более быстрой реакции
                **device_params
            ):
                while self.running:
                    try:
                        data = q.get(timeout=0.1)  # Уменьшен таймаут для быстрой реакции
                    except queue.Empty:
                        continue
                    
                    # Быстрое вычисление RMS
                    rms = np.sqrt(np.mean(data**2))
                    raw_level = min(1.0, rms * 10 * self.sensitivity)
                    
                    # Улучшенное подавление шума
                    if raw_level < self.noise_gate_threshold:
                        raw_level = 0.0
                    
                    # Применяем сглаживание для уменьшения дрожания
                    smoothed_level = self._smooth_level(raw_level)
                    
                    self._level = smoothed_level
                    if self.callback:
                        try:
                            # Передаем сглаженный уровень для более стабильной работы
                            self.callback(smoothed_level)
                        except Exception as e:
                            logger.error(f"Error in callback: {e}")
        except Exception as e:
            logger.error(f"Audio capture error: {e}")
            self._simulate_loop()
