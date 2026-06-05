import threading
import time
import os
import io
import math
import random
import sys
import cv2
import numpy as np
from PIL import Image as PILImage
from PIL import ImageSequence

# Определение базовой директории
if getattr(sys, 'frozen', False):
    BASE_DIR = os.path.dirname(sys.executable)
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# Импортируем логирование из utils
from utils import setup_logging
logger = setup_logging('renderer')


class Renderer:
    def __init__(self, width=700, height=700, fps=60):
        self.width = width
        self.height = height
        self.fps = fps
        self._running = False
        self._thread = None
        self._frame_bytes = None
        self._lock = threading.Lock()
        self.model = None
        self.model_dir = None
        self.audio_level = 0.0

        # Параметры для эффектов
        self.mouth_states = []  # Список состояний рта, будет загружаться из модели
        self.noise_gate = 0.01
        self.effects = {
            'blink': True,
            'random_effect': True,
            'bounce': False,
            'shake': False,
            'pulse': False,
            'wave': False
        }

        # Параметры эффекта волны
        self.wave_enabled = False
        self.wave_amplitude = 3.0
        self.wave_frequency = 0.5
        self.wave_speed = 1.0
        self.wave_num_frames = 5

        # Предрассчитанные кадры искажения для статичных изображений
        self._wave_frames_cache = {}
        self._gif_wave_frames_cache = {}
        self._current_wave_frame = 0
        self._wave_frame_timer = 0

        # Idle режим
        self.idle_enabled = False
        self.idle_timeout = 60.0
        self.last_activity_time = time.time()
        self.idle_brightness = 0.5

        self.groups = {}

        # Кэширование
        self._layer_cache = {}
        self._gif_cache = {}
        self._visible_layers_cache = []
        self._visible_layers_cache_time = 0
        self._cache_ttl = 0.033  # 33ms

        # Таймеры для анимаций
        self._blink_timers = {}
        self._blink_until = {}
        self._random_timers = {}
        self._random_current = {}

        # Инициализация пустого фона
        self._background = np.zeros((height, width, 4), dtype=np.uint8)

        # Проверка поддержки OpenCL
        self.use_umat = False
        try:
            if cv2.ocl.haveOpenCL():
                cv2.ocl.setUseOpenCL(True)
                self.use_umat = True
                logger.info("OpenCL acceleration enabled")
            else:
                logger.warning("OpenCL not available, using CPU rendering")
        except:
            logger.warning("OpenCL initialization failed, using CPU rendering")

        logger.info(f"Renderer initialized with dynamic mouth states support")

    def set_mouth_states(self, mouth_states):
        """Устанавливает состояния рта из модели"""
        self.mouth_states = mouth_states
        # Сбрасываем кэш видимых слоев
        self._visible_layers_cache_time = 0
        logger.info(f"Mouth states updated: {len(mouth_states)} states")

    def set_noise_gate(self, threshold):
        self.noise_gate = threshold
        logger.info(f"Noise gate threshold set: {threshold}")

    def set_effects(self, effects):
        self.effects.update(effects)
        logger.info(f"Effects updated: {self.effects}")

    def set_idle(self, enabled, timeout):
        self.idle_enabled = enabled
        self.idle_timeout = timeout
        self.last_activity_time = time.time()
        logger.info(f"Idle mode set: enabled={enabled}, timeout={timeout}")

    def start(self):
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        logger.info("Renderer started")

    def stop(self):
        self._running = False
        if self._thread:
            self._thread.join(timeout=1.0)
        logger.info("Renderer stopped")

    def _load_image_cv2(self, file_path, scale=1.0, rotation=0, flip_h=False, flip_v=False):
        """Загружает изображение с использованием PIL (поддержка Unicode) и преобразует в OpenCV."""
        try:
            # Загружаем через PIL с явным преобразованием в RGBA
            pil_img = PILImage.open(file_path).convert('RGBA')
            # Конвертируем в numpy array (формат PIL: RGBA)
            np_img = np.array(pil_img)
            # Меняем порядок каналов RGB -> BGR (OpenCV использует BGRA)
            img = cv2.cvtColor(np_img, cv2.COLOR_RGBA2BGRA)
        except Exception as e:
            logger.warning(
                f"PIL failed to load {file_path}, falling back to OpenCV: {e}")
            # Резервный вариант – OpenCV
            img = cv2.imread(file_path, cv2.IMREAD_UNCHANGED)
            if img is None:
                logger.error(
                    f"Failed to load image with both PIL and OpenCV: {file_path}")
                # Placeholder (красный квадрат)
                placeholder = np.zeros((100, 100, 4), dtype=np.uint8)
                placeholder[:, :, 0] = 255
                placeholder[:, :, 3] = 128
                return placeholder
            # Приводим к 4 каналам, если нужно
            if img.shape[2] == 3:
                img = cv2.cvtColor(img, cv2.COLOR_BGR2BGRA)
            elif img.shape[2] == 1:
                img = cv2.cvtColor(img, cv2.COLOR_GRAY2BGRA)

        # Далее применяем трансформации (scale, rotation, flip) – код остаётся без изменений
        # Применяем трансформации
        if scale != 1.0 and scale > 0:
            new_width = max(1, int(img.shape[1] * scale))
            new_height = max(1, int(img.shape[0] * scale))
            img = cv2.resize(img, (new_width, new_height),
                             interpolation=cv2.INTER_LINEAR)

        if rotation != 0:
            center = (img.shape[1] // 2, img.shape[0] // 2)
            matrix = cv2.getRotationMatrix2D(center, rotation, 1.0)
            cos_val = np.abs(matrix[0, 0])
            sin_val = np.abs(matrix[0, 1])
            new_width = int(
                (img.shape[1] * cos_val) + (img.shape[0] * sin_val))
            new_height = int(
                (img.shape[1] * sin_val) + (img.shape[0] * cos_val))
            matrix[0, 2] += (new_width / 2) - center[0]
            matrix[1, 2] += (new_height / 2) - center[1]
            img = cv2.warpAffine(img, matrix, (new_width, new_height),
                                 flags=cv2.INTER_LINEAR,
                                 borderMode=cv2.BORDER_CONSTANT,
                                 borderValue=(0, 0, 0, 0))

        if flip_h:
            img = cv2.flip(img, 1)
        if flip_v:
            img = cv2.flip(img, 0)

        # Оптимизация размера
        if img.shape[0] > 2048 or img.shape[1] > 2048:
            scale_factor = min(2048/img.shape[0], 2048/img.shape[1])
            new_width = int(img.shape[1] * scale_factor)
            new_height = int(img.shape[0] * scale_factor)
            img = cv2.resize(img, (new_width, new_height),
                             interpolation=cv2.INTER_AREA)

        return img

    def _load_gif_frames_pil(self, file_path, layer_name, layer_data):
        """Загружает GIF с использованием PIL для правильной обработки анимации"""
        try:
            pil_img = PILImage.open(file_path)
            if not pil_img.is_animated:
                # Это статичное изображение, конвертируем в OpenCV формат
                cv_img = cv2.cvtColor(
                    np.array(pil_img.convert('RGBA')), cv2.COLOR_RGBA2BGRA)

                # Применяем трансформации к статичному GIF
                scale = float(layer_data.get('scale', 1.0))
                rotation = int(layer_data.get('rotation', 0))
                flip_h = bool(layer_data.get('flip_horizontal', False))
                flip_v = bool(layer_data.get('flip_vertical', False))

                if scale != 1.0 and scale > 0:
                    new_width = max(1, int(cv_img.shape[1] * scale))
                    new_height = max(1, int(cv_img.shape[0] * scale))
                    cv_img = cv2.resize(
                        cv_img, (new_width, new_height), interpolation=cv2.INTER_LINEAR)

                if rotation != 0:
                    center = (cv_img.shape[1] // 2, cv_img.shape[0] // 2)
                    matrix = cv2.getRotationMatrix2D(center, rotation, 1.0)
                    cos_val = np.abs(matrix[0, 0])
                    sin_val = np.abs(matrix[0, 1])
                    new_width = int(
                        (cv_img.shape[1] * cos_val) + (cv_img.shape[0] * sin_val))
                    new_height = int(
                        (cv_img.shape[1] * sin_val) + (cv_img.shape[0] * cos_val))
                    matrix[0, 2] += (new_width / 2) - center[0]
                    matrix[1, 2] += (new_height / 2) - center[1]
                    cv_img = cv2.warpAffine(cv_img, matrix, (new_width, new_height),
                                            flags=cv2.INTER_LINEAR,
                                            borderMode=cv2.BORDER_CONSTANT,
                                            borderValue=(0, 0, 0, 0))

                if flip_h:
                    cv_img = cv2.flip(cv_img, 1)
                if flip_v:
                    cv_img = cv2.flip(cv_img, 0)

                # Добавляем в layer_cache как обычное изображение
                self._layer_cache[layer_name] = {
                    'name': layer_name,
                    'original_name': layer_data.get('name', layer_name),
                    'image': cv_img,
                    'is_gif': False,
                    'is_static_gif': True,
                    'x': int(layer_data.get('x', 0)),
                    'y': int(layer_data.get('y', 0)),
                    'alpha': float(layer_data.get('alpha', 1.0)),
                    'group': layer_data.get('group'),
                    'visible': layer_data.get('visible', True),
                    'index': layer_data.get('index', 999)
                }
                return

            # Это анимированный GIF
            frames = []
            frame_times = []

            for frame_idx in range(pil_img.n_frames):
                pil_img.seek(frame_idx)

                # Получаем длительность кадра
                try:
                    duration = pil_img.info['duration'] / 1000.0  # в секундах
                except:
                    duration = 0.1  # значение по умолчанию

                # Конвертируем в RGBA
                frame = pil_img.convert('RGBA')

                # Конвертируем в numpy array
                frame_array = np.array(frame)

                # Конвертируем в формат OpenCV (RGB -> BGR)
                bgr_frame = cv2.cvtColor(
                    frame_array[:, :, :3], cv2.COLOR_RGB2BGR)
                rgba_frame = np.dstack([bgr_frame, frame_array[:, :, 3]])

                # Применяем трансформации к каждому кадру
                scale = float(layer_data.get('scale', 1.0))
                rotation = int(layer_data.get('rotation', 0))
                flip_h = bool(layer_data.get('flip_horizontal', False))
                flip_v = bool(layer_data.get('flip_vertical', False))

                if scale != 1.0 and scale > 0:
                    new_width = max(1, int(rgba_frame.shape[1] * scale))
                    new_height = max(1, int(rgba_frame.shape[0] * scale))
                    rgba_frame = cv2.resize(
                        rgba_frame, (new_width, new_height), interpolation=cv2.INTER_LINEAR)

                if rotation != 0:
                    center = (rgba_frame.shape[1] //
                              2, rgba_frame.shape[0] // 2)
                    matrix = cv2.getRotationMatrix2D(center, rotation, 1.0)
                    cos_val = np.abs(matrix[0, 0])
                    sin_val = np.abs(matrix[0, 1])
                    new_width = int(
                        (rgba_frame.shape[1] * cos_val) + (rgba_frame.shape[0] * sin_val))
                    new_height = int(
                        (rgba_frame.shape[1] * sin_val) + (rgba_frame.shape[0] * cos_val))
                    matrix[0, 2] += (new_width / 2) - center[0]
                    matrix[1, 2] += (new_height / 2) - center[1]
                    rgba_frame = cv2.warpAffine(rgba_frame, matrix, (new_width, new_height),
                                                flags=cv2.INTER_LINEAR,
                                                borderMode=cv2.BORDER_CONSTANT,
                                                borderValue=(0, 0, 0, 0))

                if flip_h:
                    rgba_frame = cv2.flip(rgba_frame, 1)
                if flip_v:
                    rgba_frame = cv2.flip(rgba_frame, 0)

                frames.append(rgba_frame)
                frame_times.append(duration)

            self._gif_cache[layer_name] = {
                'frames': frames,
                'frame_times': frame_times,
                'current_frame': 0,
                'last_update': time.time(),
                'x': int(layer_data.get('x', 0)),
                'y': int(layer_data.get('y', 0)),
                'alpha': float(layer_data.get('alpha', 1.0)),
                'group': layer_data.get('group'),
                'visible': layer_data.get('visible', True),
                'index': layer_data.get('index', 999),
                'original_name': layer_data.get('name', layer_name)
            }

            # Также добавляем запись в layer_cache чтобы слой учитывался при рендеринге
            self._layer_cache[layer_name] = {
                'name': layer_name,
                'original_name': layer_data.get('name', layer_name),
                'image': None,  # Будет получаться из _gif_cache
                'is_gif': True,
                'is_static_gif': False,
                'x': int(layer_data.get('x', 0)),
                'y': int(layer_data.get('y', 0)),
                'alpha': float(layer_data.get('alpha', 1.0)),
                'group': layer_data.get('group'),
                'visible': layer_data.get('visible', True),
                'index': layer_data.get('index', 999)
            }

            logger.info(
                f"Loaded animated GIF {layer_name} with {len(frames)} frames using PIL")

        except Exception as e:
            logger.error(f"Error loading GIF {file_path} with PIL: {e}")
            # Создаем placeholder
            placeholder = np.zeros((100, 100, 4), dtype=np.uint8)
            placeholder[:, :, 0] = 255  # Красный
            placeholder[:, :, 3] = 128  # Полупрозрачность

            self._gif_cache[layer_name] = {
                'frames': [placeholder],
                'frame_times': [0.1],
                'current_frame': 0,
                'last_update': time.time(),
                'x': int(layer_data.get('x', 0)),
                'y': int(layer_data.get('y', 0)),
                'alpha': float(layer_data.get('alpha', 1.0)),
                'group': layer_data.get('group'),
                'visible': layer_data.get('visible', True),
                'index': layer_data.get('index', 999),
                'original_name': layer_data.get('name', layer_name)
            }

            self._layer_cache[layer_name] = {
                'name': layer_name,
                'original_name': layer_data.get('name', layer_name),
                'image': None,
                'is_gif': True,
                'is_static_gif': False,
                'x': int(layer_data.get('x', 0)),
                'y': int(layer_data.get('y', 0)),
                'alpha': float(layer_data.get('alpha', 1.0)),
                'group': layer_data.get('group'),
                'visible': layer_data.get('visible', True),
                'index': layer_data.get('index', 999)
            }

    def load_model(self, model_json, model_dir):
        """Загружает модель с предварительной обработкой всех изображений"""
        self.model = model_json
        self.model_dir = model_dir

        self.width = model_json.get('width', 700)
        self.height = model_json.get('height', 700)

        # Загружаем состояния рта
        self.mouth_states = model_json.get('mouth_states', [])

        # Очищаем кэши
        self._layer_cache.clear()
        self._gif_cache.clear()
        self._wave_frames_cache.clear()
        self._gif_wave_frames_cache.clear()
        self._visible_layers_cache = []
        self._visible_layers_cache_time = 0

        # Очищаем таймеры
        self._blink_timers.clear()
        self._blink_until.clear()
        self._random_timers.clear()
        self._random_current.clear()

        # Инициализируем фон
        self._background = np.zeros(
            (self.height, self.width, 4), dtype=np.uint8)

        # Загружаем слои
        for idx, layer in enumerate(self.model.get('layers', [])):
            filename = layer.get('file')
            if not filename:
                continue

            file_path = os.path.join(self.model_dir, filename)
            if not os.path.exists(file_path):
                logger.warning(f"File not found: {file_path}")
                continue

            try:
                scale = float(layer.get('scale', 1.0))
                rotation = int(layer.get('rotation', 0))
                flip_h = bool(layer.get('flip_horizontal', False))
                flip_v = bool(layer.get('flip_vertical', False))

                # Проверяем, является ли это GIF
                is_gif = filename.lower().endswith(('.gif', '.apng'))

                layer_name = layer.get('name', f'layer_{idx}')
                unique_name = layer_name
                counter = 1
                while unique_name in self._layer_cache or unique_name in self._gif_cache:
                    unique_name = f"{layer_name}_{counter}"
                    counter += 1

                # Собираем данные слоя
                layer_data = {
                    'name': layer.get('name', f'layer_{idx}'),
                    'x': int(layer.get('x', 0)),
                    'y': int(layer.get('y', 0)),
                    'alpha': float(layer.get('alpha', 1.0)),
                    'scale': scale,
                    'rotation': rotation,
                    'flip_horizontal': flip_h,
                    'flip_vertical': flip_v,
                    'group': layer.get('group'),
                    'visible': layer.get('visible', True),
                    'index': idx
                }

                if is_gif:
                    # Используем PIL для загрузки GIF - это самый надежный способ
                    self._load_gif_frames_pil(
                        file_path, unique_name, layer_data)
                else:
                    image = self._load_image_cv2(
                        file_path, scale, rotation, flip_h, flip_v)
                    if image is not None:
                        self._layer_cache[unique_name] = {
                            'name': unique_name,
                            'original_name': layer_name,
                            'image': image,
                            'is_gif': False,
                            'is_static_gif': False,
                            'x': int(layer.get('x', 0)),
                            'y': int(layer.get('y', 0)),
                            'alpha': float(layer.get('alpha', 1.0)),
                            'group': layer.get('group'),
                            'visible': layer.get('visible', True),
                            'index': idx
                        }

            except Exception as e:
                logger.error(f"Error loading layer {filename}: {e}")

        # Организуем группы
        self.groups = {g.get('name'): g for g in self.model.get('groups', [])}

        # ИНИЦИАЛИЗАЦИЯ ТАЙМЕРОВ
        current_time = time.time()

        for group_name, group in self.groups.items():
            # Таймеры для моргания
            blink_freq = float(group.get("blink_freq", 0.0))
            if blink_freq > 0.001:
                # Первое моргание через случайное время от 2 до 6 секунд
                self._blink_timers[group_name] = current_time + \
                    random.uniform(2.0, 6.0)
                self._blink_until[group_name] = 0.0  # Пока не моргаем
            else:
                self._blink_timers[group_name] = 0.0
                self._blink_until[group_name] = 0.0

            # Таймеры для случайных эффектов
            if group.get("random_effect", False):
                # Первая смена через случайное время от min до max
                random_min = float(group.get("random_min", 5.0))
                random_max = float(group.get("random_max", 10.0))
                self._random_timers[group_name] = current_time + \
                    random.uniform(random_min, random_max)
                self._random_current[group_name] = None

        # Предрассчитываем кадры эффекта "Волна" для статичных изображений и GIF
        if self.wave_enabled:
            self._precalculate_wave_frames()

        logger.info(
            f"Model loaded: {model_json.get('name', 'unnamed')} with {len(self.mouth_states)} mouth states")
        logger.info(
            f"Layers: {len(self._layer_cache)} total, GIFs: {len([k for k, v in self._layer_cache.items() if v.get('is_gif')])}")

    def _precalculate_wave_frames(self):
        """Предрассчитывает кадры эффекта 'Волна' для всех слоев"""
        self._wave_frames_cache.clear()
        self._gif_wave_frames_cache.clear()

        # Предрассчитываем для статичных изображений
        for unique_name, layer_info in self._layer_cache.items():
            # Пропускаем GIF-изображения и слои без изображения
            if layer_info.get('is_gif', False) or layer_info.get('image') is None:
                continue

            original_image = layer_info.get('image')

            # Создаем wave_num_frames вариантов искажения для статичных изображений
            for frame_idx in range(self.wave_num_frames):
                cache_key = (unique_name, frame_idx)
                distorted_img = self._create_discrete_wave_effect(
                    original_image.copy(), frame_idx)
                if distorted_img is not None:
                    self._wave_frames_cache[cache_key] = distorted_img

        # Предрассчитываем для GIF-изображений
        for gif_name, gif_info in self._gif_cache.items():
            frames = gif_info.get('frames', [])
            if not frames:
                continue

            logger.info(
                f"Precalculating wave frames for GIF {gif_name} ({len(frames)} frames)")

            # Для каждого кадра GIF создаем wave_num_frames искажений
            for gif_frame_idx, original_frame in enumerate(frames):
                for wave_frame_idx in range(self.wave_num_frames):
                    cache_key = (gif_name, gif_frame_idx, wave_frame_idx)

                    # Создаем искаженную версию кадра
                    distorted_frame = self._create_discrete_wave_effect(
                        original_frame.copy(), wave_frame_idx)
                    if distorted_frame is not None:
                        self._gif_wave_frames_cache[cache_key] = distorted_frame

        logger.info(
            f"Precalculated wave frames: {len(self._wave_frames_cache)} static, {len(self._gif_wave_frames_cache)} GIF")

    def _create_discrete_wave_effect(self, image, frame_index):
        """Создает дискретный эффект волны - разные искажения для разных frame_index"""
        if image is None or image.shape[0] == 0 or image.shape[1] == 0:
            return image

        try:
            # Конвертируем в numpy array для обработки
            img_array = image.copy()
            height, width = img_array.shape[:2]

            if self.wave_num_frames <= 1 or frame_index == 0:
                return img_array  # Нет искажения для первого кадра или если только 1 кадр

            # Создаем сетку координат
            x_coords = np.arange(width)
            y_coords = np.arange(height)
            xx, yy = np.meshgrid(x_coords, y_coords)

            # Каждый frame_index дает разное искажение
            # Разные комбинации синусов и косинусов для разных frame_index
            phase = (frame_index / self.wave_num_frames) * 2 * math.pi

            # Для каждого индекса - свой тип искажения
            if frame_index == 1:
                # Легкое волнообразное искажение по X
                dx = np.sin(xx * self.wave_frequency * 0.02 +
                            phase) * self.wave_amplitude * 0.5
                dy = np.zeros_like(dx)
            elif frame_index == 2:
                # Легкое волнообразное искажение по Y
                dx = np.zeros_like(xx, dtype=np.float32)
                dy = np.cos(yy * self.wave_frequency * 0.015 +
                            phase) * self.wave_amplitude * 0.5
            elif frame_index == 3:
                # Диагональное искажение
                dx = np.sin((xx + yy) * self.wave_frequency *
                            0.01 + phase) * self.wave_amplitude * 0.7
                dy = np.cos((xx + yy) * self.wave_frequency *
                            0.01 + phase) * self.wave_amplitude * 0.7
            elif frame_index == 4:
                # Спиральное искажение
                center_x = width / 2
                center_y = height / 2
                dist_x = xx - center_x
                dist_y = yy - center_y
                angle = np.arctan2(dist_y, dist_x)
                distance = np.sqrt(dist_x**2 + dist_y**2)

                dx = np.sin(angle + distance * 0.01 + phase) * \
                    self.wave_amplitude * 0.3
                dy = np.cos(angle + distance * 0.01 + phase) * \
                    self.wave_amplitude * 0.3
            else:
                # Для других индексов - комбинированный эффект
                dx = np.sin(xx * self.wave_frequency * 0.01 +
                            phase) * self.wave_amplitude
                dy = np.cos(yy * self.wave_frequency * 0.008 +
                            phase) * self.wave_amplitude * 0.7

            # Нормализуем смещения
            dx = np.clip(dx, -self.wave_amplitude, self.wave_amplitude)
            dy = np.clip(dy, -self.wave_amplitude, self.wave_amplitude)

            # Создаем новые координаты
            new_x = np.clip(xx + dx, 0, width - 1).astype(np.int32)
            new_y = np.clip(yy + dy, 0, height - 1).astype(np.int32)

            # Применяем искажение
            distorted_array = np.zeros_like(img_array)
            for c in range(img_array.shape[2]):
                distorted_array[:, :, c] = img_array[new_y, new_x, c]

            return distorted_array

        except Exception as e:
            logger.error(f"Error creating discrete wave effect: {e}")
            return image

    def set_audio_level(self, level):
        """Устанавливает уровень аудио с учетом noise gate"""
        if level < self.noise_gate:
            level = 0.0
        self.audio_level = max(0.0, min(1.0, float(level)))

        # Обновляем время последней активности для idle режима
        if self.idle_enabled and level > self._get_min_active_threshold():
            self.last_activity_time = time.time()

        # Сбрасываем кэш видимых слоев
        self._visible_layers_cache_time = 0

    def _get_min_active_threshold(self):
        """Возвращает минимальный порог активности для idle режима"""
        if not self.mouth_states:
            return 0.0

        # Находим минимальный порог среди активных состояний (исключая самое первое, которое может быть тишиной)
        min_threshold = float('inf')
        for state in self.mouth_states:
            if state.get('active', True):
                threshold = state.get('threshold', 0.0)
                # Игнорируем порог 0 (тишина)
                if threshold > 0 and threshold < min_threshold:
                    min_threshold = threshold

        return min_threshold if min_threshold != float('inf') else 0.0

    def _get_current_mouth_state_index(self):
        """Определяет текущий индекс состояния рта на основе аудио уровня"""
        if not self.mouth_states:
            return -1

        # Находим состояние с максимальным порогом, который не превышает уровень
        current_idx = -1
        for i, state in enumerate(self.mouth_states):
            if state.get('active', True) and self.audio_level >= state.get('threshold', 0.0):
                current_idx = i

        return current_idx

    def _choose_group_child(self, group, current_time):
        """Выбирает дочерний элемент группы на основе логики и таймеров"""
        group_name = group.get("name")
        logic = group.get("logic", {})

        # Эффект моргания
        if self.effects.get('blink', True) and group.get("blink_freq", 0.0) > 0.001:
            blink_freq = float(group.get("blink_freq", 0.0))

            # Инициализация таймеров если их нет
            if group_name not in self._blink_timers:
                self._blink_timers[group_name] = current_time + \
                    random.uniform(2.0, 6.0)
                self._blink_until[group_name] = 0.0

            # Проверяем, нужно ли начать моргание
            if current_time > self._blink_timers.get(group_name, 0):
                # Моргание длится 0.12 секунды
                self._blink_until[group_name] = current_time + 0.12
                # Следующее моргание через blink_freq секунд
                self._blink_timers[group_name] = current_time + blink_freq

            # Показываем состояние моргания если сейчас время моргания
            if current_time < self._blink_until.get(group_name, 0):
                blink_target = logic.get("blink")
                if blink_target:
                    return blink_target

        # Рандомный эффект
        if (self.effects.get('random_effect', True) and
            group.get("random_effect", False) and
                current_time > self._random_timers.get(group_name, 0)):

            random_min = float(group.get("random_min", 5.0))
            random_max = float(group.get("random_max", 10.0))

            children = group.get("children", [])
            if children:
                # Исключаем специальные состояния из логики
                available = []
                for child_name in children:
                    if child_name not in logic.values():
                        available.append(child_name)

                if available:
                    chosen = random.choice(available)
                    self._random_current[group_name] = chosen
                    # Следующая смена через случайный интервал
                    interval = random.uniform(random_min, random_max)
                    self._random_timers[group_name] = current_time + interval
                    return chosen

        # Возвращаем текущее случайное состояние если оно есть
        if self._random_current.get(group_name) and current_time < self._random_timers.get(group_name, 0):
            return self._random_current[group_name]

        # Стандартная логика на основе состояния рта
        current_state_idx = self._get_current_mouth_state_index()
        state_key = f"state_{current_state_idx}"

        if state_key in logic and logic[state_key]:
            return logic[state_key]
        elif "open" in logic and logic["open"]:
            return logic["open"]

        # Если ничего не подошло и все состояния неактивны, возвращаем None
        if current_state_idx == -1:
            return None

        # Возвращаем первого дочернего элемента
        children = group.get("children", [])
        return children[0] if children else None

    def _get_visible_layers(self):
        """Определяет видимые слои на основе текущего состояния"""
        current_time = time.time()

        # Используем кэш если он еще актуален
        if current_time - self._visible_layers_cache_time < self._cache_ttl and self._visible_layers_cache:
            return self._visible_layers_cache

        visible_layers = []
        processed_groups = set()

        def process_group(group_name):
            if group_name in processed_groups:
                return None
            processed_groups.add(group_name)

            group = self.groups.get(group_name)
            if not group:
                return None

            chosen = self._choose_group_child(group, current_time)

            if chosen is None:
                # Если не выбрано состояние, ничего не показываем из этой группы
                return None

            if chosen in self.groups:
                # Рекурсивно обрабатываем дочернюю группу
                return process_group(chosen)
            else:
                # Это слой, добавляем в видимые
                return chosen

        # Обрабатываем корневые группы
        root_groups = [name for name, g in self.groups.items()
                       if not g.get('parent')]
        for group_name in root_groups:
            result = process_group(group_name)
            if result and result in self._layer_cache:
                visible_layers.append(result)

        # Добавляем слои без групп
        for layer_name, layer_info in self._layer_cache.items():
            if layer_info.get('group') is None and layer_info.get('visible', True):
                visible_layers.append(layer_name)

        # Сортируем по индексу для правильного порядка наложения
        visible_layers.sort(key=lambda name:
                            self._layer_cache.get(name, {}).get('index', 999) if name in self._layer_cache else 999)

        # Обновляем кэш
        self._visible_layers_cache = visible_layers
        self._visible_layers_cache_time = current_time

        return visible_layers

    def _get_current_gif_frame(self, layer_name):
        """Получает текущий кадр GIF без обновления таймера"""
        if layer_name not in self._gif_cache:
            return None

        gif_info = self._gif_cache[layer_name]
        frames = gif_info['frames']

        if not frames:
            return None

        return frames[gif_info['current_frame']]

    def _render_frame(self):
        """Рендерит один кадр с использованием OpenCV"""
        # Создаем фон
        frame = np.zeros((self.height, self.width, 4), dtype=np.uint8)

        # Получаем видимые слои
        visible_layers = self._get_visible_layers()

        # Если нет видимых слоев, возвращаем пустой фон
        if not visible_layers:
            return frame

        # Обновляем таймер смены кадров эффекта "Волна" только если эффект включен
        if self.wave_enabled and self.wave_speed > 0:
            now = time.time()
            interval = 1.0 / self.wave_speed  # Интервал между сменой кадров

            if now - self._wave_frame_timer > interval:
                self._current_wave_frame = (
                    self._current_wave_frame + 1) % self.wave_num_frames
                self._wave_frame_timer = now

        # Рендерим каждый слой
        for layer_name in visible_layers:
            # Получаем информацию о слое из layer_cache
            if layer_name not in self._layer_cache:
                continue

            layer_info = self._layer_cache[layer_name]

            if not layer_info.get('visible', True):
                continue

            x = layer_info.get('x', 0)
            y = layer_info.get('y', 0)
            alpha = layer_info.get('alpha', 1.0)
            is_gif = layer_info.get('is_gif', False)

            # Получаем изображение (для GIF - текущий кадр из gif_cache)
            image = None
            if is_gif:
                # Для анимированных GIF получаем текущий кадр
                if layer_name in self._gif_cache:
                    gif_info = self._gif_cache[layer_name]

                    # ВАЖНО: Обновляем текущий кадр GIF ДО применения эффектов
                    current_gif_frame = gif_info['current_frame']
                    last_update = gif_info['last_update']
                    frames = gif_info['frames']
                    frame_times = gif_info['frame_times']

                    current_time = time.time()

                    # Проверяем, нужно ли переключить кадр GIF
                    if frames and frame_times:
                        if current_time - last_update > frame_times[current_gif_frame]:
                            # Переходим к следующему кадру
                            new_frame = (current_gif_frame + 1) % len(frames)
                            gif_info['current_frame'] = new_frame
                            gif_info['last_update'] = current_time
                            current_gif_frame = new_frame  # Обновляем локальную переменную

                    # Применяем эффект волны если он включен
                    if self.wave_enabled and frames:
                        # Берем предрасчитанный кадр из кэша
                        cache_key = (layer_name, current_gif_frame,
                                     self._current_wave_frame)
                        if cache_key in self._gif_wave_frames_cache:
                            image = self._gif_wave_frames_cache[cache_key]
                        else:
                            # Если нет в кэше, получаем оригинальный кадр и создаем эффект на лету
                            original_frame = frames[current_gif_frame]
                            if original_frame is not None:
                                image = self._create_discrete_wave_effect(
                                    original_frame.copy(), self._current_wave_frame)
                                # Сохраняем в кэш для будущего использования
                                self._gif_wave_frames_cache[cache_key] = image
                    else:
                        if frames:
                            image = frames[current_gif_frame]

                    # Также получаем параметры из gif_cache
                    x = gif_info.get('x', x)
                    y = gif_info.get('y', y)
                    alpha = gif_info.get('alpha', alpha)
            else:
                # Для обычных изображений
                if self.wave_enabled:
                    # Берем предрасчитанный кадр из кэша
                    cache_key = (layer_name, self._current_wave_frame)
                    if cache_key in self._wave_frames_cache:
                        image = self._wave_frames_cache[cache_key]
                    else:
                        # Если нет в кэше, создаем на лету
                        original_image = layer_info.get('image')
                        if original_image is not None:
                            image = self._create_discrete_wave_effect(
                                original_image.copy(), self._current_wave_frame)
                else:
                    image = layer_info.get('image')

            if image is None:
                continue

            # Применяем прозрачность слоя
            if alpha < 1.0:
                image = image.copy()
                image[:, :, 3] = (image[:, :, 3] * alpha).astype(np.uint8)

            # Эффекты
            bounce_intensity = 0
            if self.effects.get('bounce', False):
                bounce_intensity = int(
                    math.sin(time.time() * 5) * min(10, self.audio_level * 20))

            if self.effects.get('shake', False):
                shake_intensity = min(1.0, self.audio_level * 5)
                offset_x = int((random.random() - 0.5) * 10 * shake_intensity)
                offset_y = int((random.random() - 0.5) * 10 *
                               shake_intensity) + bounce_intensity
            else:
                offset_x, offset_y = 0, bounce_intensity

            if self.effects.get('pulse', False):
                pulse_scale = 1.0 + (math.sin(time.time() * 5)
                                     * 0.1 * self.audio_level)
                new_size = (
                    int(image.shape[1] * pulse_scale), int(image.shape[0] * pulse_scale))
                if new_size[0] > 0 and new_size[1] > 0:
                    image = cv2.resize(
                        image, new_size, interpolation=cv2.INTER_LINEAR)

            h, w = image.shape[:2]
            px = (self.width - w) // 2 + x + offset_x
            py = (self.height - h) // 2 + y + offset_y

            # Проверяем границы
            if px + w <= 0 or px >= self.width or py + h <= 0 or py >= self.height:
                continue

            # Определяем область перекрытия
            x1 = max(0, px)
            y1 = max(0, py)
            x2 = min(self.width, px + w)
            y2 = min(self.height, py + h)

            if x1 >= x2 or y1 >= y2:
                continue

            # Координаты в исходном изображении
            sx1 = x1 - px
            sy1 = y1 - py
            sx2 = sx1 + (x2 - x1)
            sy2 = sy1 + (y2 - y1)

            if sx1 < 0 or sy1 < 0 or sx2 > w or sy2 > h:
                continue

            try:
                # Извлекаем области
                overlay = image[sy1:sy2, sx1:sx2]
                background = frame[y1:y2, x1:x2]

                # Альфа-композитинг
                alpha_overlay = overlay[:, :, 3].astype(np.float32) / 255.0
                alpha_background = background[:, :, 3].astype(
                    np.float32) / 255.0

                # Вычисляем итоговую альфа
                alpha_out = alpha_overlay + \
                    alpha_background * (1 - alpha_overlay)
                # Избегаем деления на ноль
                alpha_out = np.maximum(alpha_out, 0.001)

                # Комбинируем цвета
                for c in range(3):  # RGB каналы
                    background[:, :, c] = (
                        overlay[:, :, c] * alpha_overlay +
                        background[:, :, c] *
                        alpha_background * (1 - alpha_overlay)
                    ) / alpha_out

                # Комбинируем альфа канал
                background[:, :, 3] = (alpha_out * 255).astype(np.uint8)

                # Обновляем кадр
                frame[y1:y2, x1:x2] = background

            except Exception as e:
                logger.error(f"Error compositing layer {layer_name}: {e}")

        # Применяем idle эффект
        if self.idle_enabled:
            current_time = time.time()
            if current_time - self.last_activity_time > self.idle_timeout:
                # Уменьшаем яркость
                brightness_factor = self.idle_brightness
                # Применяем только к RGB каналам
                frame[:, :, :3] = (frame[:, :, :3] *
                                   brightness_factor).astype(np.uint8)

        return frame

    def get_frame_bytes(self):
        """Возвращает текущий кадр в формате PNG"""
        with self._lock:
            return self._frame_bytes

    def _loop(self):
        """Основной цикл рендеринга"""
        frame_count = 0
        last_fps_time = time.time()
        fps = 0
        target_frame_time = 1.0 / self.fps

        while self._running:
            if not self.model or not self.model_dir:
                time.sleep(0.1)
                continue

            frame_start = time.time()

            try:
                # Рендерим кадр
                frame = self._render_frame()

                # Конвертируем в PNG
                success, buffer = cv2.imencode(
                    '.png', frame, [cv2.IMWRITE_PNG_COMPRESSION, 1])
                if success:
                    with self._lock:
                        self._frame_bytes = buffer.tobytes()

            except Exception as e:
                logger.error(f"Error in rendering loop: {e}")
                # Создаем черный кадр в случае ошибки
                error_frame = np.zeros(
                    (self.height, self.width, 4), dtype=np.uint8)
                success, buffer = cv2.imencode(
                    '.png', error_frame, [cv2.IMWRITE_PNG_COMPRESSION, 1])
                if success:
                    with self._lock:
                        self._frame_bytes = buffer.tobytes()

            # Считаем FPS
            frame_count += 1
            current_time = time.time()
            if current_time - last_fps_time >= 1.0:
                fps = frame_count
                frame_count = 0
                last_fps_time = current_time

                if fps < self.fps * 0.8:
                    logger.warning(f"Low FPS: {fps}/{self.fps}")

            # Контроль FPS
            frame_time = current_time - frame_start
            sleep_time = target_frame_time - frame_time

            if sleep_time > 0:
                time.sleep(sleep_time)
            elif frame_time > target_frame_time * 1.5:
                logger.warning(
                    f"Frame render took {frame_time*1000:.2f}ms, target: {target_frame_time*1000:.2f}ms")

    def set_wave(self, enabled, amplitude=3.0, frequency=0.5, speed=1.0):
        """Включает/выключает эффект 'Волна' и устанавливает параметры"""
        old_enabled = self.wave_enabled
        self.wave_enabled = enabled
        self.wave_amplitude = amplitude
        self.wave_frequency = frequency
        # Ограничиваем скорость от 0 до 5
        self.wave_speed = max(0, min(5, speed))

        if enabled:
            # Если скорость = 0, отключаем эффект
            if self.wave_speed == 0:
                self.wave_enabled = False
                logger.info("Wave effect disabled because speed is 0")
            else:
                # Пересчитываем кадры эффекта только если он был выключен или параметры изменились
                if not old_enabled or speed != self.wave_speed:
                    self._precalculate_wave_frames()
        else:
            # Очищаем кэш и сбрасываем текущий кадр
            self._wave_frames_cache.clear()
            self._gif_wave_frames_cache.clear()
            self._current_wave_frame = 0
            self._wave_frame_timer = 0

            # Сбрасываем кэш видимых слоев
            self._visible_layers_cache_time = 0

        logger.info(
            f"Wave effect: enabled={enabled}, amplitude={amplitude}, frequency={frequency}, speed={speed}")
