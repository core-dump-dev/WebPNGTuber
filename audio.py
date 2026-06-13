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
import importlib

# Определение базовой директории
if getattr(sys, 'frozen', False):
    BASE_DIR = os.path.dirname(sys.executable)
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# Импортируем логирование из utils
from utils import setup_logging
logger = setup_logging('audio')


def list_host_apis():
    """
    Возвращает список доступных Host API.
    Каждый элемент: {'index': int, 'name': str}
    """
    apis = []
    try:
        for idx, info in enumerate(sd.query_hostapis()):
            apis.append({'index': idx, 'name': info.get('name', f'API {idx}')})
    except Exception as e:
        logger.error(f"Error listing host APIs: {e}")
    return apis


def list_audio_devices(host_api_index=None):
    """
    Возвращает список аудиоустройств.
    Если host_api_index указан, возвращает только устройства этого API.
    Каждый элемент: {'index': int, 'name': str, 'is_output': bool, 'hostapi': int}
    """
    devices = []
    try:
        all_devices = sd.query_devices()
        for idx, dev in enumerate(all_devices):
            # Пропускаем устройства без входных каналов (для микрофона нужны входные)
            if dev.get('max_input_channels', 0) == 0:
                continue
            api_idx = dev.get('hostapi')
            if host_api_index is not None and api_idx != host_api_index:
                continue
            devices.append({
                'index': idx,
                'name': dev.get('name', f'Device {idx}'),
                'is_output': False,
                'hostapi': api_idx
            })
    except Exception as e:
        logger.error(f"Error listing audio devices: {e}")
    # Добавляем виртуальное устройство "По умолчанию" (индекс None)
    devices.insert(0, {'index': None, 'name': 'По умолчанию',
                   'is_output': False, 'hostapi': None})
    return devices


class AudioProcessor:
    def __init__(self, callback=None, device=None, host_api_index=None):
        self.callback = callback
        self.running = False
        self._level = 0.0
        self._thread = None
        # может быть именем (str) или индексом (int)
        self.device = device
        # выбранный Host API (int или None)
        self.host_api_index = host_api_index
        self.noise_gate_threshold = 0.01
        self.device_index = None      # реальный индекс устройства в sounddevice
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

        # Если передан host_api_index, но device является именем, нужно разрешить позже
        # При старте будем вызывать _resolve_device()
        self._resolve_device()

    def _resolve_device(self):
        """
        Преобразует self.device (имя или индекс) и self.host_api_index
        в реальный индекс устройства для sounddevice.
        """
        if self.device is None:
            self.device_index = None
            return

        # Если device уже число (индекс) и не None
        if isinstance(self.device, int):
            self.device_index = self.device
            return

        # device - строка (имя)
        device_name = self.device
        if device_name == "По умолчанию":
            self.device_index = None
            return

        try:
            devices = sd.query_devices()
            # Фильтруем по host_api_index, если задан
            for idx, dev in enumerate(devices):
                if dev.get('max_input_channels', 0) == 0:
                    continue
                if self.host_api_index is not None and dev.get('hostapi') != self.host_api_index:
                    continue
                if dev.get('name') == device_name:
                    self.device_index = idx
                    return
            # Если не нашли, пробуем без фильтрации API
            for idx, dev in enumerate(devices):
                if dev.get('max_input_channels', 0) == 0:
                    continue
                if dev.get('name') == device_name:
                    self.device_index = idx
                    return
        except Exception as e:
            logger.error(f"Error resolving device: {e}")
        self.device_index = None

    def set_device_by_api(self, host_api_index, device_index):
        """
        Устанавливает устройство через указание Host API и индекса устройства.
        device_index может быть None (По умолчанию) или целым числом.
        """
        self.host_api_index = host_api_index
        if device_index is None:
            self.device = "По умолчанию"
            self.device_index = None
        else:
            # Попытаемся получить имя устройства для совместимости со старыми настройками
            try:
                devices = sd.query_devices()
                if 0 <= device_index < len(devices):
                    self.device = devices[device_index].get('name')
                else:
                    self.device = str(device_index)
            except:
                self.device = str(device_index)
            self.device_index = device_index
        self._resolve_device()

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
        # Разрешаем устройство (если еще не разрешено)
        self._resolve_device()

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
            self.running = False
