import threading, time
import os, io, math, random, sys
import logging
import logging.handlers
import cv2
import numpy as np
import imageio
from PIL import Image as PILImage
from PIL import ImageSequence

# Определение базовой директории
if getattr(sys, 'frozen', False):
    BASE_DIR = os.path.dirname(sys.executable)
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# Создание папки для логов
LOGS_DIR = os.path.join(BASE_DIR, "logs")
os.makedirs(LOGS_DIR, exist_ok=True)

# Настройка логирования для renderer
def setup_renderer_logging():
    logger = logging.getLogger('renderer')
    logger.setLevel(logging.DEBUG)
    formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    log_file = os.path.join(LOGS_DIR, 'renderer.log')
    file_handler = logging.handlers.RotatingFileHandler(
        log_file, maxBytes=1048576, backupCount=5
    )
    file_handler.setFormatter(formatter)
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    logger.addHandler(file_handler)
    logger.addHandler(console_handler)
    return logger

logger = setup_renderer_logging()

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
        self.thresholds = {
            'silent': 0.05,
            'whisper': 0.25,
            'normal': 0.6,
            'shout': 0.8
        }
        self.noise_gate = 0.01
        self.active_states = {
            'silent': True,
            'whisper': True,
            'normal': True,
            'shout': True
        }
        self.effects = {
            'blink': True,
            'random_effect': True,
            'bounce': False,
            'shake': False,
            'pulse': False,
            'wave': False
        }
        
        # Параметры эффекта волны (оригинальные)
        self.wave_enabled = False
        self.wave_amplitude = 3.0  # Амплитуда искажения
        self.wave_frequency = 0.5  # Частота волн
        self.wave_speed = 1.0  # Скорость анимации
        
        # Предрассчитанные кадры искажения (4 варианта) - только для статичных изображений
        self._wave_frames_cache = {}  # Ключ: (layer_name, frame_index)
        self._current_wave_frame = 0
        self._wave_frame_timer = 0
        self._wave_frame_interval = 1.0  # Смена кадра раз в секунду
        self._wave_last_update = 0
        
        # Idle режим
        self.idle_enabled = False
        self.idle_timeout = 60.0
        self.last_activity_time = time.time()
        self.idle_brightness = 0.5
        
        # Кэширование
        self._layer_cache = {}
        self._gif_cache = {}
        self._visible_layers_cache = []
        self._visible_layers_cache_time = 0
        self._cache_ttl = 0.033  # 33ms
        
        # Таймеры для анимаций - ИСПРАВЛЕНО: правильная инициализация
        self._blink_timers = {}  # Когда следующее моргание
        self._blink_until = {}   # До какого времени показывать моргание
        self._random_timers = {} # Когда следующая случайная смена
        self._random_current = {} # Текущее случайное состояние
        
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
        
        logger.info(f"Renderer initialized with fixed timers and GIF support")
    
    def set_idle(self, enabled, timeout):
        self.idle_enabled = enabled
        self.idle_timeout = timeout
        self.last_activity_time = time.time()
        logger.info(f"Idle mode set: enabled={enabled}, timeout={timeout}")
    
    def set_noise_gate(self, threshold):
        self.noise_gate = threshold
        logger.info(f"Noise gate threshold set: {threshold}")
    
    def set_effects(self, effects):
        self.effects.update(effects)
        logger.info(f"Effects updated: {self.effects}")
    
    def set_thresholds(self, thresholds):
        self.thresholds.update(thresholds)
        logger.info(f"Thresholds updated: {self.thresholds}")
    
    def set_active_states(self, active_states):
        self.active_states.update(active_states)
        logger.info(f"Active states updated: {self.active_states}")
    
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
        """Загружает и преобразует изображение с использованием OpenCV"""
        try:
            # Загружаем изображение
            img = cv2.imread(file_path, cv2.IMREAD_UNCHANGED)
            if img is None:
                logger.error(f"Failed to load image: {file_path}")
                return None
            
            # Конвертируем в RGBA если нужно
            if img.shape[2] == 3:
                img = cv2.cvtColor(img, cv2.COLOR_BGR2BGRA)
            elif img.shape[2] == 1:
                img = cv2.cvtColor(img, cv2.COLOR_GRAY2BGRA)
            
            # Применяем трансформации
            if scale != 1.0 and scale > 0:
                new_width = max(1, int(img.shape[1] * scale))
                new_height = max(1, int(img.shape[0] * scale))
                img = cv2.resize(img, (new_width, new_height), interpolation=cv2.INTER_LINEAR)
            
            if rotation != 0:
                # Поворот с использованием матрицы аффинных преобразований
                center = (img.shape[1] // 2, img.shape[0] // 2)
                matrix = cv2.getRotationMatrix2D(center, rotation, 1.0)
                cos_val = np.abs(matrix[0, 0])
                sin_val = np.abs(matrix[0, 1])
                new_width = int((img.shape[1] * cos_val) + (img.shape[0] * sin_val))
                new_height = int((img.shape[1] * sin_val) + (img.shape[0] * cos_val))
                matrix[0, 2] += (new_width / 2) - center[0]
                matrix[1, 2] += (new_height / 2) - center[1]
                img = cv2.warpAffine(img, matrix, (new_width, new_height), 
                                   flags=cv2.INTER_LINEAR,
                                   borderMode=cv2.BORDER_CONSTANT,
                                   borderValue=(0, 0, 0, 0))
            
            if flip_h:
                img = cv2.flip(img, 1)  # 1 = горизонтальное отражение
            if flip_v:
                img = cv2.flip(img, 0)  # 0 = вертикальное отражение
            
            # Оптимизация памяти - уменьшаем размер если слишком большое
            if img.shape[0] > 2048 or img.shape[1] > 2048:
                scale_factor = min(2048/img.shape[0], 2048/img.shape[1])
                new_width = int(img.shape[1] * scale_factor)
                new_height = int(img.shape[0] * scale_factor)
                img = cv2.resize(img, (new_width, new_height), interpolation=cv2.INTER_AREA)
            
            return img
            
        except Exception as e:
            logger.error(f"Error loading image {file_path}: {e}")
            # Создаем placeholder
            placeholder = np.zeros((100, 100, 4), dtype=np.uint8)
            placeholder[:, :, 0] = 255  # Красный
            placeholder[:, :, 3] = 128  # Полупрозрачность
            return placeholder
    
    def _load_gif_frames_pil(self, file_path, layer_name):
        """Загружает GIF с использованием PIL для правильной обработки анимации"""
        try:
            pil_img = PILImage.open(file_path)
            if not pil_img.is_animated:
                # Это статичное изображение, конвертируем в OpenCV формат
                cv_img = cv2.cvtColor(np.array(pil_img.convert('RGBA')), cv2.COLOR_RGBA2BGRA)
                self._layer_cache[layer_name] = {
                    'name': layer_name,
                    'image': cv_img,
                    'is_gif': False,
                    'x': 0, 'y': 0,
                    'alpha': 1.0,
                    'index': 999
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
                bgr_frame = cv2.cvtColor(frame_array[:, :, :3], cv2.COLOR_RGB2BGR)
                rgba_frame = np.dstack([bgr_frame, frame_array[:, :, 3]])
                
                frames.append(rgba_frame)
                frame_times.append(duration)
            
            self._gif_cache[layer_name] = {
                'frames': frames,
                'frame_times': frame_times,
                'current_frame': 0,
                'last_update': time.time()
            }
            
            logger.info(f"Loaded GIF {layer_name} with {len(frames)} frames using PIL")
            
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
                'last_update': time.time()
            }
    
    def load_model(self, model_json, model_dir):
        """Загружает модель с предварительной обработкой всех изображений"""
        self.model = model_json
        self.model_dir = model_dir
        
        self.width = model_json.get('width', 700)
        self.height = model_json.get('height', 700)
        
        # Очищаем кэши
        self._layer_cache.clear()
        self._gif_cache.clear()
        self._wave_frames_cache.clear()
        self._visible_layers_cache = []
        self._visible_layers_cache_time = 0
        
        # Очищаем таймеры
        self._blink_timers.clear()
        self._blink_until.clear()
        self._random_timers.clear()
        self._random_current.clear()
        
        # Инициализируем фон
        self._background = np.zeros((self.height, self.width, 4), dtype=np.uint8)
        
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
                
                if is_gif:
                    # Используем PIL для загрузки GIF - это самый надежный способ
                    self._load_gif_frames_pil(file_path, unique_name)
                    is_gif_success = unique_name in self._gif_cache
                else:
                    image = self._load_image_cv2(file_path, scale, rotation, flip_h, flip_v)
                    if image is not None:
                        self._layer_cache[unique_name] = {
                            'name': unique_name,
                            'original_name': layer_name,
                            'image': image,
                            'is_gif': False,
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
        
        # ИНИЦИАЛИЗАЦИЯ ТАЙМЕРОВ - ИСПРАВЛЕНО
        current_time = time.time()
        
        for group_name, group in self.groups.items():
            # Таймеры для моргания
            blink_freq = float(group.get("blink_freq", 0.0))
            if blink_freq > 0.001:
                # Первое моргание через случайное время от 2 до 6 секунд
                self._blink_timers[group_name] = current_time + random.uniform(2.0, 6.0)
                self._blink_until[group_name] = 0.0  # Пока не моргаем
            else:
                self._blink_timers[group_name] = 0.0
                self._blink_until[group_name] = 0.0
            
            # Таймеры для случайных эффектов
            if group.get("random_effect", False):
                # Первая смена через случайное время от min до max
                random_min = float(group.get("random_min", 5.0))
                random_max = float(group.get("random_max", 10.0))
                self._random_timers[group_name] = current_time + random.uniform(random_min, random_max)
                self._random_current[group_name] = None
        
        # Предрассчитываем кадры эффекта "Волна" только для статичных изображений
        if self.wave_enabled:
            self._precalculate_wave_frames()
        
        logger.info(f"Model loaded: {model_json.get('name', 'unnamed')} with fixed timers and GIF support")
    
    def _precalculate_wave_frames(self):
        """Предрассчитывает 4 кадра эффекта 'Волна' для статичных слоев (оригинальная версия)"""
        self._wave_frames_cache.clear()
        now = time.time()
        self._wave_last_update = now
        
        for unique_name, layer_info in self._layer_cache.items():
            original_image = layer_info.get('image')
            if original_image is None:
                continue
            
            # Пропускаем GIF-изображения, они будут обрабатываться в реальном времени
            if layer_info.get('is_gif', False):
                continue
            
            # Создаем 4 варианта искажения для статичных изображений
            for frame_idx in range(4):
                cache_key = (unique_name, frame_idx)
                distorted_img = self._create_wave_effect(original_image.copy(), frame_idx)
                if distorted_img is not None:
                    self._wave_frames_cache[cache_key] = distorted_img
        
        logger.info(f"Precalculated {len(self._wave_frames_cache)} wave frames for static images")
    
    def _create_wave_effect(self, image, frame_index):
        """Создает эффект волны/искажения (оригинальная версия из PIL кода)"""
        if image is None or image.shape[0] == 0 or image.shape[1] == 0:
            return image
        
        try:
            # Конвертируем в numpy array для обработки
            img_array = image.copy()
            height, width = img_array.shape[:2]
            
            # Создаем сетку координат
            x_coords = np.arange(width)
            y_coords = np.arange(height)
            xx, yy = np.meshgrid(x_coords, y_coords)
            
            # Параметры искажения для этого кадра
            phase = (frame_index / 4.0) * 2 * math.pi
            time_offset = self._wave_last_update * self.wave_speed
            
            # Волновые искажения (несколько частот для более естественного вида)
            wave1 = np.sin(xx * self.wave_frequency * 0.01 + time_offset + phase) * self.wave_amplitude
            wave2 = np.cos(yy * self.wave_frequency * 0.008 + time_offset * 1.3 + phase) * self.wave_amplitude * 0.7
            wave3 = np.sin((xx + yy) * self.wave_frequency * 0.005 + time_offset * 0.7 + phase) * self.wave_amplitude * 0.5
            
            # Общее смещение
            dx = wave1 + wave3
            dy = wave2 + wave3 * 0.8
            
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
            logger.error(f"Error creating wave effect: {e}")
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
        voice_states = ['whisper', 'normal', 'shout']
        min_threshold = float('inf')
        
        for state in voice_states:
            if self.active_states.get(state, True):
                threshold = self.thresholds.get(state, 1.0)
                if threshold < min_threshold:
                    min_threshold = threshold
        
        return min_threshold if min_threshold != float('inf') else 0.0
    
    def _get_current_state(self):
        """Определяет текущее состояние на основе аудио уровня"""
        if self.audio_level > self.thresholds.get('shout', 0.8) and self.active_states.get('shout', True):
            return 'shout'
        elif self.audio_level > self.thresholds.get('normal', 0.6) and self.active_states.get('normal', True):
            return 'normal'
        elif self.audio_level > self.thresholds.get('whisper', 0.25) and self.active_states.get('whisper', True):
            return 'whisper'
        elif self.audio_level > self.thresholds.get('silent', 0.05) and self.active_states.get('silent', True):
            return 'silent'
        return 'silent'
    
    def _choose_group_child(self, group, current_time):
        """Выбирает дочерний элемент группы на основе логики и таймеров"""
        group_name = group.get("name")
        logic = group.get("logic", {})
        
        # Эффект моргания - ИСПРАВЛЕНО: правильная логика
        if self.effects.get('blink', True) and group.get("blink_freq", 0.0) > 0.001:
            blink_freq = float(group.get("blink_freq", 0.0))
            
            # Инициализация таймеров если их нет
            if group_name not in self._blink_timers:
                self._blink_timers[group_name] = current_time + random.uniform(2.0, 6.0)
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
        
        # Рандомный эффект - ИСПРАВЛЕНО: правильные интервалы
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
        
        # Стандартная логика на основе состояния
        current_state = self._get_current_state()
        
        if current_state in logic and self.active_states.get(current_state, True):
            return logic[current_state]
        elif "open" in logic and logic["open"]:
            return logic["open"]
        elif "normal" in logic and logic["normal"]:
            return logic["normal"]
        elif "silent" in logic and logic["silent"]:
            return logic["silent"]
        
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
            
            if not chosen:
                # Если не выбрано состояние, показываем всех детей без групп
                for layer_name, layer_info in self._layer_cache.items():
                    if layer_info.get('group') == group_name and layer_info.get('visible', True):
                        visible_layers.append(layer_name)
                return None
            
            if chosen in self.groups:
                # Рекурсивно обрабатываем дочернюю группу
                return process_group(chosen)
            else:
                # Это слой, добавляем в видимые
                return chosen
        
        # Обрабатываем корневые группы
        root_groups = [name for name, g in self.groups.items() if not g.get('parent')]
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
        """Получает текущий кадр GIF"""
        if layer_name not in self._gif_cache:
            return None
        
        gif_info = self._gif_cache[layer_name]
        frames = gif_info['frames']
        frame_times = gif_info['frame_times']
        
        if not frames:
            return None
        
        current_time = time.time()
        current_frame = gif_info['current_frame']
        last_update = gif_info['last_update']
        
        # Проверяем, нужно ли переключить кадр
        if current_time - last_update > frame_times[current_frame]:
            # Переходим к следующему кадру
            gif_info['current_frame'] = (current_frame + 1) % len(frames)
            gif_info['last_update'] = current_time
        
        return frames[gif_info['current_frame']]
    
    def _render_frame(self):
        """Рендерит один кадр с использованием OpenCV"""
        # Создаем фон
        frame = np.zeros((self.height, self.width, 4), dtype=np.uint8)
        
        # Получаем видимые слои
        visible_layers = self._get_visible_layers()
        
        # Обновляем таймер смены кадров эффекта "Волна" только если эффект включен
        if self.wave_enabled:
            now = time.time()
            self._wave_last_update = now
            
            if now - self._wave_frame_timer > self._wave_frame_interval:
                self._current_wave_frame = (self._current_wave_frame + 1) % 4
                self._wave_frame_timer = now
        
        # Рендерим каждый слой
        for layer_name in visible_layers:
            # Получаем информацию о слое
            layer_info = None
            is_gif = False
            
            if layer_name in self._layer_cache:
                layer_info = self._layer_cache[layer_name]
            elif layer_name in self._gif_cache:
                layer_info = {'is_gif': True, 'name': layer_name, 'x': 0, 'y': 0, 'alpha': 1.0}
                is_gif = True
            
            if not layer_info:
                continue
            
            x = layer_info.get('x', 0)
            y = layer_info.get('y', 0)
            alpha = layer_info.get('alpha', 1.0)
            
            # Получаем изображение (для GIF - текущий кадр)
            if is_gif:
                image = self._get_current_gif_frame(layer_name)
                if image is None:
                    continue
            else:
                image = layer_info['image']
                if image is None:
                    continue
            
            # Применяем эффект волны если он включен
            if self.wave_enabled:
                # Проверяем, является ли изображение GIF
                is_current_gif = layer_name in self._gif_cache
                
                if is_current_gif:
                    # Для GIF применяем эффект волны к текущему кадру в реальном времени
                    image = self._create_wave_effect(image.copy(), self._current_wave_frame)
                else:
                    # Для статичных изображений используем кэш
                    cache_key = (layer_name, self._current_wave_frame)
                    if cache_key in self._wave_frames_cache:
                        image = self._wave_frames_cache[cache_key]
                    else:
                        # Если нет в кэше, создаем новый
                        image = self._create_wave_effect(image.copy(), self._current_wave_frame)
                        self._wave_frames_cache[cache_key] = image
            
            if image is None:
                continue
            
            # Применяем прозрачность слоя
            if alpha < 1.0:
                image = image.copy()
                image[:, :, 3] = (image[:, :, 3] * alpha).astype(np.uint8)
            
            # Эффекты
            bounce_intensity = 0
            if self.effects.get('bounce', False):
                bounce_intensity = int(math.sin(time.time() * 5) * min(10, self.audio_level * 20))
            
            if self.effects.get('shake', False):
                shake_intensity = min(1.0, self.audio_level * 5)
                offset_x = int((random.random() - 0.5) * 10 * shake_intensity)
                offset_y = int((random.random() - 0.5) * 10 * shake_intensity) + bounce_intensity
            else:
                offset_x, offset_y = 0, bounce_intensity
            
            if self.effects.get('pulse', False):
                pulse_scale = 1.0 + (math.sin(time.time() * 5) * 0.1 * self.audio_level)
                new_size = (int(image.shape[1] * pulse_scale), int(image.shape[0] * pulse_scale))
                if new_size[0] > 0 and new_size[1] > 0:
                    image = cv2.resize(image, new_size, interpolation=cv2.INTER_LINEAR)
            
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
                alpha_background = background[:, :, 3].astype(np.float32) / 255.0
                
                # Вычисляем итоговую альфа
                alpha_out = alpha_overlay + alpha_background * (1 - alpha_overlay)
                alpha_out = np.maximum(alpha_out, 0.001)  # Избегаем деления на ноль
                
                # Комбинируем цвета
                for c in range(3):  # RGB каналы
                    background[:, :, c] = (
                        overlay[:, :, c] * alpha_overlay + 
                        background[:, :, c] * alpha_background * (1 - alpha_overlay)
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
                frame[:, :, :3] = (frame[:, :, :3] * brightness_factor).astype(np.uint8)
        
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
                success, buffer = cv2.imencode('.png', frame, [cv2.IMWRITE_PNG_COMPRESSION, 1])
                if success:
                    with self._lock:
                        self._frame_bytes = buffer.tobytes()
            
            except Exception as e:
                logger.error(f"Error in rendering loop: {e}")
                # Создаем черный кадр в случае ошибки
                error_frame = np.zeros((self.height, self.width, 4), dtype=np.uint8)
                success, buffer = cv2.imencode('.png', error_frame, [cv2.IMWRITE_PNG_COMPRESSION, 1])
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
                logger.warning(f"Frame render took {frame_time*1000:.2f}ms, target: {target_frame_time*1000:.2f}ms")
    
    def set_wave(self, enabled, amplitude=3.0, frequency=0.5, speed=1.0):
        """Включает/выключает эффект 'Волна' и устанавливает параметры (оригинальная логика)"""
        old_enabled = self.wave_enabled
        self.wave_enabled = enabled
        self.wave_amplitude = amplitude
        self.wave_frequency = frequency
        self.wave_speed = speed
        
        if enabled:
            # Пересчитываем кадры эффекта только если он был выключен
            if not old_enabled and self.model:
                self._precalculate_wave_frames()
        else:
            # Очищаем кэш и сбрасываем текущий кадр
            self._wave_frames_cache.clear()
            self._current_wave_frame = 0
            self._wave_frame_timer = 0
            
            # Сбрасываем кэш видимых слоев
            self._visible_layers_cache_time = 0
        
        logger.info(f"Wave effect: enabled={enabled}, amplitude={amplitude}, frequency={frequency}, speed={speed}")