# audio.py
import threading
import time
import numpy as np
import sounddevice as sd
import sys
import os
from datetime import datetime
from collections import deque
import queue

# Определение базовой директории
if getattr(sys, 'frozen', False):
    BASE_DIR = os.path.dirname(sys.executable)
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# Импортируем логирование из utils
from utils import setup_logging
logger = setup_logging('audio')


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

        self._smoothing_buffer = deque(maxlen=3)
        self._smoothing_alpha = 0.7
        self._last_smoothed_level = 0.0

        self._audio_queue = queue.Queue(maxsize=10)
        self._processing_thread = None
        self._buffer = np.zeros(256, dtype=np.float32)

        self._callback_lock = threading.Lock()
        self._last_callback_time = 0
        self._callback_interval = 0.01

        if getattr(sys, 'frozen', False):
            sys.stderr = open(os.devnull, 'w')

        if device and device != "По умолчанию":
            devices = sd.query_devices()
            for i, dev in enumerate(devices):
                if dev['name'] == device and dev['max_input_channels'] > 0:
                    self.device_index = i
                    break

    def set_sensitivity(self, sensitivity):
        """Установка чувствительности"""
        self.sensitivity = max(0.1, min(5.0, sensitivity))

    def start(self):
        """Запуск обработки аудио"""
        if self.running:
            return
        self.running = True
        self._thread = threading.Thread(target=self._capture_loop, daemon=True)
        self._thread.start()

    def stop(self):
        """Остановка обработки аудио"""
        self.running = False
        if self._thread:
            self._thread.join(timeout=1.0)
        self._thread = None

        if getattr(sys, 'frozen', False):
            sys.stderr = sys.__stderr__

    def _capture_loop(self):
        """Оптимизированный цикл захвата аудио"""
        def callback(indata, frames, time_info, status):
            if not self.running or status:
                return

            current_time = time.time()
            if current_time - self._last_callback_time < self._callback_interval:
                return

            with self._callback_lock:
                np.copyto(self._buffer, indata[:, 0])

                rms = np.sqrt(np.mean(self._buffer ** 2))
                raw_level = min(1.0, rms * 10 * self.sensitivity)

                if raw_level < self.noise_gate_threshold:
                    raw_level = 0.0

                self._level = self._smoothing_alpha * raw_level + \
                    (1 - self._smoothing_alpha) * self._level

                if self.callback:
                    try:
                        self.callback(self._level)
                    except Exception as e:
                        logger.error(f"Error in callback: {e}")

                self._last_callback_time = current_time

        try:
            device_params = {}
            if self.device_index is not None:
                device_params['device'] = self.device_index

            with sd.InputStream(
                channels=1,
                callback=callback,
                samplerate=22050,
                blocksize=256,
                latency='low',
                **device_params
            ):
                while self.running:
                    time.sleep(0.01)

        except Exception as e:
            logger.error(f"Audio capture error: {e}")
