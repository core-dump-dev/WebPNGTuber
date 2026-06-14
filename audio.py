import threading
import time
import numpy as np
import sounddevice as sd
import sys
import os
from collections import deque
import queue
import platform

# Попытка импорта soundcard для поддержки Loopback (захват с динамиков)
try:
    import soundcard as sc
    SOUNDCARD_AVAILABLE = True
except ImportError:
    SOUNDCARD_AVAILABLE = False

# Определение базовой директории
if getattr(sys, 'frozen', False):
    BASE_DIR = os.path.dirname(sys.executable)
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# Импортируем логирование из utils
from utils import setup_logging
logger = setup_logging('audio')


def list_host_apis():
    """Возвращает список доступных Host API (только WASAPI и MME)."""
    apis = []
    try:
        for idx, info in enumerate(sd.query_hostapis()):
            name = info.get('name', f'API {idx}')
            if 'WASAPI' in name or 'MME' in name:
                apis.append({'index': idx, 'name': name})
    except Exception as e:
        logger.error(f"Error listing host APIs: {e}")
    return apis


def is_loopback_supported(host_api_index):
    """Проверяет, поддерживает ли выбранный Host API loopback-захват (только WASAPI на Windows)."""
    if platform.system() != 'Windows':
        return False
    try:
        apis = sd.query_hostapis()
        if 0 <= host_api_index < len(apis):
            api_name = apis[host_api_index].get('name', '')
            return 'WASAPI' in api_name
    except:
        pass
    return False


def list_audio_devices(host_api_index=None):
    """Возвращает список аудиоустройств для выбранного Host API."""
    devices = []
    try:
        all_devices = sd.query_devices()
        # Устройства ввода (микрофоны)
        for idx, dev in enumerate(all_devices):
            if dev.get('max_input_channels', 0) > 0:
                api_idx = dev.get('hostapi')
                if host_api_index is not None and api_idx != host_api_index:
                    continue
                default_sr = dev.get('default_samplerate', 44100)
                devices.append({
                    'index': idx,
                    'name': f"🎤 {dev.get('name', f'Mic {idx}')}",
                    'is_output': False,
                    'hostapi': api_idx,
                    'loopback_supported': False,
                    'default_samplerate': default_sr
                })
        # Устройства вывода (динамики) - только если выбранный API поддерживает loopback
        if host_api_index is not None and is_loopback_supported(host_api_index):
            for idx, dev in enumerate(all_devices):
                if dev.get('max_output_channels', 0) > 0:
                    api_idx = dev.get('hostapi')
                    if api_idx != host_api_index:
                        continue
                    default_sr = dev.get('default_samplerate', 44100)
                    devices.append({
                        'index': idx,
                        'name': f"🔊 {dev.get('name', f'Speaker {idx}')}",
                        'is_output': True,
                        'hostapi': api_idx,
                        'loopback_supported': True,
                        'default_samplerate': default_sr
                    })
    except Exception as e:
        logger.error(f"Error listing audio devices: {e}")

    devices.insert(0, {'index': None, 'name': '🎤 По умолчанию (микрофон)', 'is_output': False,
                       'hostapi': None, 'loopback_supported': False, 'default_samplerate': 44100})
    return devices


class AudioProcessor:
    def __init__(self, callback=None, device=None, host_api_index=None):
        self.callback = callback
        self.running = False
        self._level = 0.0
        self._thread = None
        self.device = device
        self.host_api_index = host_api_index
        self.noise_gate_threshold = 0.01
        self.device_index = None
        self.is_output_device = False
        self.loopback = False
        self.sensitivity = 1.0
        self.samplerate = 44100
        self.blocksize = 256

        self._buffer = np.zeros(256, dtype=np.float32)
        self._smoothing_alpha = 0.7

        self._callback_lock = threading.Lock()
        self._last_callback_time = 0
        self._callback_interval = 0.01

        if getattr(sys, 'frozen', False):
            sys.stderr = open(os.devnull, 'w')

        self._resolve_device()

    def _resolve_device(self):
        """Определяет реальный индекс устройства и его параметры. Безопасно обрабатывает отсутствие устройств."""
        self.is_output_device = False
        self.loopback = False
        self.samplerate = 44100

        if self.device is None or self.device == "🎤 По умолчанию (микрофон)":
            self.device = "🎤 По умолчанию (микрофон)"
            try:
                default_input = sd.default.device[0]
                if default_input is not None and default_input >= 0:
                    sd.query_devices(default_input)
                    self.device_index = default_input
                else:
                    self.device_index = None
            except Exception:
                self.device_index = None
            return

        if isinstance(self.device, int):
            self.device_index = self.device
        else:
            device_name = self.device
            if device_name == "🎤 По умолчанию (микрофон)":
                self.device_index = None
                return

            clean_name = device_name
            if clean_name.startswith("🎤 "):
                clean_name = clean_name[2:]
            elif clean_name.startswith("🔊 "):
                clean_name = clean_name[2:]

            try:
                devices = sd.query_devices()
                for idx, dev in enumerate(devices):
                    if self.host_api_index is not None and dev.get('hostapi') != self.host_api_index:
                        continue
                    if dev.get('name') == clean_name:
                        self.device_index = idx
                        if dev.get('max_output_channels', 0) > 0 and dev.get('max_input_channels', 0) == 0:
                            self.is_output_device = True
                            if self.host_api_index is not None and is_loopback_supported(self.host_api_index):
                                self.loopback = True
                        else:
                            self.is_output_device = False
                            self.loopback = False
                        self.samplerate = int(
                            dev.get('default_samplerate', 44100))
                        return
                # Поиск без фильтрации API
                for idx, dev in enumerate(devices):
                    if dev.get('name') == clean_name:
                        self.device_index = idx
                        if dev.get('max_output_channels', 0) > 0 and dev.get('max_input_channels', 0) == 0:
                            self.is_output_device = True
                            if self.host_api_index is not None and is_loopback_supported(self.host_api_index):
                                self.loopback = True
                        else:
                            self.is_output_device = False
                            self.loopback = False
                        self.samplerate = int(
                            dev.get('default_samplerate', 44100))
                        return
            except Exception as e:
                logger.error(f"Error resolving device: {e}")

        if self.device_index is not None:
            try:
                dev_info = sd.query_devices(self.device_index)
                if dev_info.get('max_output_channels', 0) > 0 and dev_info.get('max_input_channels', 0) == 0:
                    self.is_output_device = True
                    if self.host_api_index is not None and is_loopback_supported(self.host_api_index):
                        self.loopback = True
                self.samplerate = int(dev_info.get(
                    'default_samplerate', 44100))
            except Exception:
                logger.warning(
                    f"Device {self.device_index} disconnected. Falling back to default.")
                self.device_index = None
                self.device = "🎤 По умолчанию (микрофон)"
                self.is_output_device = False
                self.loopback = False
                self.samplerate = 44100

    def set_device_by_api(self, host_api_index, device_index, is_output=False):
        """Устанавливает устройство через указание Host API и индекса."""
        self.host_api_index = host_api_index
        if device_index is None:
            self.device = "🎤 По умолчанию (микрофон)"
            self.device_index = None
            self.is_output_device = False
            self.loopback = False
            self.samplerate = 44100
        else:
            try:
                dev_info = sd.query_devices(device_index)
                name = dev_info.get('name', f'Device {device_index}')
                self.samplerate = int(dev_info.get(
                    'default_samplerate', 44100))
                if is_output or (dev_info.get('max_output_channels', 0) > 0 and dev_info.get('max_input_channels', 0) == 0):
                    self.device = f"🔊 {name}"
                    self.is_output_device = True
                    self.loopback = is_loopback_supported(
                        host_api_index) if host_api_index is not None else False
                else:
                    self.device = f"🎤 {name}"
                    self.is_output_device = False
                    self.loopback = False
                self.device_index = device_index
            except Exception as e:
                logger.error(f"Error setting device: {e}")
                self.device = "🎤 По умолчанию (микрофон)"
                self.device_index = None
                self.is_output_device = False
                self.loopback = False
                self.samplerate = 44100
        self._resolve_device()

    def set_sensitivity(self, sensitivity):
        self.sensitivity = max(0.1, min(5.0, sensitivity))

    def start(self):
        if self.running:
            return
        self.running = True
        self._thread = threading.Thread(target=self._capture_loop, daemon=True)
        self._thread.start()

    def stop(self):
        self.running = False
        if self._thread:
            self._thread.join(timeout=1.0)
        self._thread = None
        if getattr(sys, 'frozen', False):
            sys.stderr = sys.__stderr__

    def _capture_loop(self):
        """Главный цикл захвата с автоматическим восстановлением при отключении устройств."""
        while self.running:
            try:
                if self.loopback:
                    self._capture_loop_soundcard()
                else:
                    self._capture_loop_fallback()
            except Exception as e:
                logger.error(
                    f"Capture loop crashed: {e}. Restarting in 2 seconds...")
                time.sleep(2)
                # Принудительно переопределяем устройство, возможно оно изменилось
                self._resolve_device()
                continue

    # ===================== LOOPBACK (захват с динамиков) =====================
    def _capture_loop_soundcard(self):
        """Цикл захвата звука с динамиков (loopback) через библиотеку soundcard с автоматическим восстановлением."""
        if not SOUNDCARD_AVAILABLE:
            logger.error(
                "Для захвата звука с динамиков (loopback) необходима библиотека 'soundcard'. Переключаюсь на микрофон по умолчанию.")
            self.loopback = False
            self.device_index = None
            self.device = "🎤 По умолчанию (микрофон)"
            self._capture_loop_fallback()
            return

        logger.info("Инициализация захвата звука через soundcard (Loopback)...")
        while self.running:
            try:
                all_mics = sc.all_microphones(include_loopback=True)
                target_mic = None
                clean_name = self.device or ""
                if clean_name.startswith("🔊 "):
                    clean_name = clean_name[2:]
                elif clean_name.startswith("🎤 "):
                    clean_name = clean_name[2:]

                # Поиск устройства по имени
                for mic in all_mics:
                    if clean_name and clean_name.lower() in mic.name.lower():
                        target_mic = mic
                        break

                # Если не нашли, берем первый loopback
                if target_mic is None:
                    loopback_mics = [m for m in all_mics if getattr(
                        m, 'isloopback', False)]
                    if loopback_mics:
                        target_mic = loopback_mics[0]
                        logger.info(
                            f"Устройство не найдено, выбран первый доступный loopback: {target_mic.name}")
                    else:
                        logger.error(
                            "Loopback-устройства не найдены. Убедитесь, что в Windows включены стерео микшеры.")
                        # Пробуем через fallback
                        self.loopback = False
                        self._capture_loop_fallback()
                        return

                logger.info(
                    f"Выбрано устройство для loopback: {target_mic.name}")
                with target_mic.recorder(samplerate=self.samplerate, channels=1) as mic:
                    while self.running:
                        data = mic.record(numframes=self.blocksize)
                        if not self.running:
                            break

                        audio_data = data.flatten()
                        rms = np.sqrt(np.mean(audio_data ** 2))
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
            except Exception as e:
                logger.error(
                    f"Ошибка soundcard: {e}. Переподключение через 2 секунды...")
                time.sleep(2)
                continue

    # ===================== FALLBACK (обычный микрофон) с отказоустойчивостью =====================
    def _capture_loop_fallback(self):
        """Цикл захвата с умным ожиданием подключения устройства (sounddevice) и автоматическим восстановлением."""
        # Цикл ожидания, пока не появится рабочее устройство
        while self.running and self.device_index is None:
            logger.debug(
                "No valid input device found. Waiting for connection or manual refresh...")
            time.sleep(2.0)
            self._resolve_device()  # Периодически проверяем, не появилось ли устройство

        if not self.running:
            return

        # Пробуем разные частоты дискретизации
        samplerates_to_try = [self.samplerate, 44100, 48000, 22050, 16000]
        samplerates_to_try = list(dict.fromkeys(samplerates_to_try))

        for sr in samplerates_to_try:
            blocksize = max(64, min(int(sr * 0.0116), 1024))
            buffer = np.zeros(blocksize, dtype=np.float32)

            def callback(indata, frames, time_info, status):
                if not self.running or status:
                    return
                current_time = time.time()
                if current_time - self._last_callback_time < self._callback_interval:
                    return
                with self._callback_lock:
                    copy_len = min(frames, len(buffer))
                    buffer[:copy_len] = indata[:copy_len, 0]
                    rms = np.sqrt(np.mean(buffer[:copy_len] ** 2))
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

            device_params = {}
            if self.device_index is not None:
                device_params['device'] = self.device_index

            try:
                with sd.InputStream(
                    channels=1,
                    callback=callback,
                    samplerate=sr,
                    blocksize=blocksize,
                    latency='low',
                    **device_params
                ):
                    self.samplerate = sr
                    self.blocksize = blocksize
                    self._buffer = buffer
                    logger.info(
                        f"Audio stream opened successfully: sr={sr}, device={self.device_index}")
                    # Работаем, пока поток жив
                    while self.running:
                        time.sleep(0.01)
                    return  # Успешная работа, выходим
            except Exception as e:
                logger.warning(
                    f"Failed to open device {self.device_index} with sr={sr}: {e}")
                continue

        # Если все частоты не подошли, возможно устройство отключилось
        logger.warning("Audio stream failed. Resetting device...")
        self.device_index = None
        self._resolve_device()
        # Не выходим из функции, а дадим внешнему while в _capture_loop перезапустить
        time.sleep(2)
