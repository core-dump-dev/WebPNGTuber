import threading, time
import numpy as np
import sounddevice as sd
import sys
import os
import logging
import logging.handlers
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
    logger.setLevel(logging.INFO)  # Уменьшили уровень логирования
    
    formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    
    log_file = os.path.join(LOGS_DIR, 'audio.log')
    file_handler = logging.handlers.RotatingFileHandler(
        log_file, maxBytes=1048576, backupCount=5
    )
    file_handler.setFormatter(formatter)
    
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    
    logger.addHandler(file_handler)
    logger.addHandler(console_handler)
    
    return logger

logger = setup_audio_logging()

class SmoothingFilter:
    """Фильтр для сглаживания аудиоуровня"""
    def __init__(self, window_size: int = 5):
        self.window_size = window_size
        self.values = deque(maxlen=window_size)
        self.smoothed_value = 0.0
    
    def add(self, value: float) -> float:
        """Добавляет новое значение и возвращает сглаженное"""
        self.values.append(value)
        
        if len(self.values) < self.window_size:
            # Используем среднее пока не набрали достаточно значений
            self.smoothed_value = sum(self.values) / len(self.values)
        else:
            # Взвешенное среднее с большим весом у последних значений
            weights = np.linspace(0.5, 1.5, len(self.values))
            weights = weights / weights.sum()
            self.smoothed_value = np.average(list(self.values), weights=weights)
        
        return self.smoothed_value
    
    def reset(self):
        """Сбрасывает фильтр"""
        self.values.clear()
        self.smoothed_value = 0.0

class OptimizedAudioProcessor:
    def __init__(self, callback=None, device=None):
        self.callback = callback
        self.running = False
        self._level = 0.0
        self._thread = None
        self.device = device
        
        # Параметры захвата
        self.samplerate = 22050  # Уменьшили частоту дискретизации
        self.blocksize = 1024
        self.channels = 1
        
        # Подавление шума
        self.noise_gate_threshold = 0.01
        self.device_index = None
        self.sensitivity = 1.0
        
        # Фильтры
        self.smoothing_filter = SmoothingFilter(window_size=3)
        
        # Оптимизации
        self.last_callback_time = 0
        self.callback_interval = 0.033  # ~30 FPS для колбэков
        
        # Подавление вывода ошибок
        if getattr(sys, 'frozen', False):
            sys.stderr = open(os.devnull, 'w')
        
        # Получение индекса устройства
        if device and device != "По умолчанию":
            devices = sd.query_devices()
            for i, dev in enumerate(devices):
                if dev['name'] == device and dev['max_input_channels'] > 0:
                    self.device_index = i
                    break
        
        logger.info(f"Audio processor initialized with device: {device}")
    
    def set_sensitivity(self, sensitivity):
        """Установка чувствительности"""
        self.sensitivity = max(0.1, min(5.0, sensitivity))
    
    def start(self):
        """Запуск обработки аудио"""
        if self.running:
            return
        
        self.running = True
        self.smoothing_filter.reset()
        self._thread = threading.Thread(target=self._optimized_capture_loop, daemon=True)
        self._thread.start()
    
    def stop(self):
        """Остановка обработки аудио"""
        self.running = False
        if self._thread:
            self._thread.join(timeout=1.0)
        self._thread = None
        
        # Восстановление stderr
        if getattr(sys, 'frozen', False):
            sys.stderr = sys.__stderr__
    
    def _optimized_capture_loop(self):
        """Оптимизированный цикл захвата аудио"""
        import queue
        import threading
        
        q = queue.Queue(maxsize=10)  # Ограничиваем размер очереди
        
        def audio_callback(indata, frames, time_info, status):
            """Колбэк для аудиопотока"""
            if not self.running:
                return
            
            try:
                # Быстрое вычисление RMS
                data = indata.copy()
                # Используем float32 для ускорения вычислений
                data_f32 = data.astype(np.float32, copy=False)
                
                # Вычисляем энергию сигнала
                energy = np.mean(data_f32 ** 2)
                rms = np.sqrt(energy)
                
                # Применяем чувствительность и сглаживание
                raw_level = min(1.0, rms * 10 * self.sensitivity)
                smoothed_level = self.smoothing_filter.add(raw_level)
                
                # Подавление шума
                if smoothed_level < self.noise_gate_threshold:
                    smoothed_level = 0.0
                
                # Обновляем уровень
                self._level = smoothed_level
                
                # Отправляем колбэк с ограничением частоты
                current_time = time.time()
                if (current_time - self.last_callback_time >= self.callback_interval and
                    self.callback):
                    
                    try:
                        self.callback(smoothed_level)
                    except Exception as e:
                        logger.error(f"Error in callback: {e}")
                    
                    self.last_callback_time = current_time
                    
            except Exception as e:
                logger.error(f"Error in audio callback: {e}")
        
        try:
            device_params = {}
            if self.device_index is not None:
                device_params['device'] = self.device_index
            
            # Используем более легковесные параметры
            with sd.InputStream(
                channels=self.channels,
                callback=audio_callback,
                samplerate=self.samplerate,  # Уменьшенная частота
                blocksize=self.blocksize,
                dtype='float32',  # Используем float32 вместо float64
                **device_params
            ):
                # Простой цикл ожидания
                while self.running:
                    time.sleep(0.1)  # Проверяем каждые 100мс
                    
        except Exception as e:
            logger.error(f"Audio capture error: {e}")
            self._fallback_loop()
    
    def _fallback_loop(self):
        """Фолбэк-режим (для тестирования)"""
        t = 0.0
        while self.running:
            t += 0.1
            
            # Симуляция разных уровней
            if int(t) % 10 < 3:
                level = 0.1
            elif int(t) % 10 < 6:
                level = 0.4
            elif int(t) % 10 < 8:
                level = 0.7
            else:
                level = 0.9
            
            level = level * self.sensitivity
            self._level = level
            
            # Отправляем колбэк с ограничением частоты
            current_time = time.time()
            if (current_time - self.last_callback_time >= self.callback_interval and
                self.callback):
                
                try:
                    self.callback(level)
                except Exception as e:
                    logger.error(f"Error in callback: {e}")
                
                self.last_callback_time = current_time
            
            time.sleep(0.1)

# Сохраняем обратную совместимость
AudioProcessor = OptimizedAudioProcessor