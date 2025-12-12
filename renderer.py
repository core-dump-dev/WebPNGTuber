import threading, time
from PIL import Image, ImageEnhance, ImageSequence
import os, io, math, random
import logging
import logging.handlers
import sys
import numpy as np
from collections import deque
from typing import Dict, List, Set, Optional, Tuple
import hashlib

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
    logger.setLevel(logging.INFO)  # Изменили на INFO для уменьшения логов
    
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

class FrameBuffer:
    """Буфер кадра для минимизации аллокаций памяти"""
    def __init__(self, width: int, height: int):
        self.width = width
        self.height = height
        self.buffer = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        self.bytes_buffer = bytearray(width * height * 4)
    
    def get_image(self) -> Image.Image:
        """Возвращает текущее изображение из буфера"""
        return self.buffer
    
    def update_from_bytes(self, data: bytes):
        """Обновляет буфер из байтов"""
        if len(data) == len(self.bytes_buffer):
            self.bytes_buffer[:] = data
            self.buffer = Image.frombytes("RGBA", (self.width, self.height), bytes(self.bytes_buffer))
    
    def clear(self):
        """Очищает буфер"""
        self.buffer = Image.new("RGBA", (self.width, self.height), (0, 0, 0, 0))
        self.bytes_buffer[:] = bytes([0] * len(self.bytes_buffer))

class OptimizedImageCache:
    """Кэш для оптимизированного хранения изображений"""
    def __init__(self):
        self._cache: Dict[str, Image.Image] = {}
        self._gif_cache: Dict[str, Tuple[List[Image.Image], List[float]]] = {}
        self._transformed_cache: Dict[str, Image.Image] = {}
        self._image_hash: Dict[str, str] = {}
    
    def get_image(self, key: str, path: str, transform_params: Dict) -> Optional[Image.Image]:
        """Получает изображение из кэша или загружает его"""
        transform_key = f"{key}_{hash(str(transform_params))}"
        
        if transform_key in self._transformed_cache:
            return self._transformed_cache[transform_key]
        
        if key not in self._cache:
            try:
                self._cache[key] = Image.open(path).convert("RGBA")
            except Exception as e:
                logger.error(f"Ошибка загрузки изображения {path}: {e}")
                return None
        
        # Применяем трансформации
        img = self._cache[key].copy()
        if transform_params.get('flip_h', False):
            img = img.transpose(Image.FLIP_LEFT_RIGHT)
        if transform_params.get('flip_v', False):
            img = img.transpose(Image.FLIP_TOP_BOTTOM)
        if transform_params.get('scale', 1.0) != 1.0:
            new_w = max(1, int(img.width * transform_params['scale']))
            new_h = max(1, int(img.height * transform_params['scale']))
            img = img.resize((new_w, new_h), Image.LANCZOS)
        if transform_params.get('rotation', 0) != 0:
            img = img.rotate(transform_params['rotation'], expand=True, resample=Image.BICUBIC)
        
        self._transformed_cache[transform_key] = img
        return img
    
    def get_gif_frames(self, key: str, path: str, transform_params: Dict) -> Optional[Tuple[List[Image.Image], List[float]]]:
        """Получает кадры GIF из кэша"""
        transform_key = f"{key}_gif_{hash(str(transform_params))}"
        
        if transform_key in self._gif_cache:
            return self._gif_cache[transform_key]
        
        try:
            with Image.open(path) as gif:
                frames = []
                durations = []
                
                for i in range(gif.n_frames):
                    gif.seek(i)
                    frame = gif.copy().convert("RGBA")
                    
                    # Применяем трансформации
                    if transform_params.get('flip_h', False):
                        frame = frame.transpose(Image.FLIP_LEFT_RIGHT)
                    if transform_params.get('flip_v', False):
                        frame = frame.transpose(Image.FLIP_TOP_BOTTOM)
                    if transform_params.get('scale', 1.0) != 1.0:
                        new_w = max(1, int(frame.width * transform_params['scale']))
                        new_h = max(1, int(frame.height * transform_params['scale']))
                        frame = frame.resize((new_w, new_h), Image.LANCZOS)
                    if transform_params.get('rotation', 0) != 0:
                        frame = frame.rotate(transform_params['rotation'], expand=True, resample=Image.BICUBIC)
                    
                    frames.append(frame)
                    durations.append(gif.info.get('duration', 100) / 1000.0)
                
                self._gif_cache[transform_key] = (frames, durations)
                return frames, durations
        except Exception as e:
            logger.error(f"Ошибка загрузки GIF {path}: {e}")
        
        return None
    
    def clear(self):
        """Очищает кэш"""
        self._cache.clear()
        self._gif_cache.clear()
        self._transformed_cache.clear()

class Renderer:
    def __init__(self, width=700, height=700, fps=60):
        self.width = width
        self.height = height
        self.fps = fps
        self.target_frame_time = 1.0 / fps
        
        self._running = False
        self._thread = None
        self._frame_bytes = None
        self._lock = threading.Lock()
        
        # Оптимизированные структуры данных
        self.model = None
        self.model_dir = None
        self.audio_level = 0.0
        
        # Кэширование
        self.image_cache = OptimizedImageCache()
        self.frame_buffer = FrameBuffer(width, height)
        
        # Буфер для временных операций
        self.temp_buffer = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        
        # Пороговые значения
        self.thresholds = {
            'silent': 0.05,
            'whisper': 0.25,
            'normal': 0.6,
            'shout': 0.8
        }
        
        self.noise_gate = 0.01
        
        # Активные состояния
        self.active_states = {
            'silent': True,
            'whisper': True,
            'normal': True,
            'shout': True
        }
        
        self.state_order = ['silent', 'whisper', 'normal', 'shout']
        self.effects = {}
        
        # Инициализация таймеров
        self.group_blink_timers = {}
        self.group_blink_until = {}
        self.group_random_timers = {}
        self.group_random_current = {}
        
        # GIF анимация
        self._gif_current_frame = {}
        self._gif_last_update = {}
        
        # Idle режим
        self.idle_enabled = False
        self.idle_timeout = 60.0
        self.last_activity_time = time.time()
        self.idle_brightness = 0.5
        
        # Быстрые структуры для доступа
        self.layers_by_name = {}
        self.groups_by_name = {}
        self.layers_by_group = {}
        
        # Кэш видимых слоев
        self._visible_layers_cache = []
        self._visible_cache_time = 0
        self._cache_valid_for = 0.1  # Секунд
        
        # Статистика производительности
        self.frame_count = 0
        self.last_stats_time = time.time()
        self.avg_frame_time = 0
        
        logger.info(f"Renderer initialized with {width}x{height} @ {fps} FPS")
    
    def set_idle(self, enabled, timeout):
        self.idle_enabled = enabled
        self.idle_timeout = timeout
        self.last_activity_time = time.time()
    
    def set_noise_gate(self, threshold):
        self.noise_gate = threshold
    
    def set_effects(self, effects):
        self.effects = effects
    
    def set_thresholds(self, thresholds):
        self.thresholds = thresholds
    
    def set_active_states(self, active_states):
        self.active_states = active_states
    
    def start(self):
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._optimized_loop, daemon=True)
        self._thread.start()
        logger.info("Renderer started")
    
    def stop(self):
        self._running = False
        if self._thread:
            self._thread.join(timeout=1.0)
        logger.info("Renderer stopped")
    
    def load_model(self, model_json, model_dir):
        self.model = model_json
        self.model_dir = model_dir
        
        # Обновляем размеры
        self.width = model_json.get('width', 700)
        self.height = model_json.get('height', 700)
        
        # Пересоздаем буферы с новыми размерами
        self.frame_buffer = FrameBuffer(self.width, self.height)
        self.temp_buffer = Image.new("RGBA", (self.width, self.height), (0, 0, 0, 0))
        
        # Очищаем кэши
        self.image_cache.clear()
        
        # Инициализируем структуры быстрого доступа
        self.layers_by_name = {}
        self.groups_by_name = {}
        self.layers_by_group = {}
        
        # Загружаем все изображения в кэш - исправляем здесь
        for layer in model_json.get("layers", []):
            name = layer.get("name")
            filename = layer.get("file")
            
            if not name or not filename:
                continue
            
            filepath = os.path.join(model_dir, filename)
            if not os.path.exists(filepath):
                continue
            
            # Сохраняем в быстрые структуры
            # Фильтруем только нужные поля
            filtered_layer = {}
            valid_fields = ["name", "file", "x", "y", "scale", "rotation", 
                        "flip_horizontal", "flip_vertical", "visible", 
                        "is_gif", "group"]
            
            for field in valid_fields:
                if field in layer:
                    filtered_layer[field] = layer[field]
            
            self.layers_by_name[name] = filtered_layer
            
            group_name = layer.get("group")
            if group_name:
                if group_name not in self.layers_by_group:
                    self.layers_by_group[group_name] = []
                self.layers_by_group[group_name].append(name)
            
            # Предварительная загрузка в кэш
            transform_params = {
                'scale': float(filtered_layer.get("scale", 1.0)),
                'rotation': int(filtered_layer.get("rotation", 0)),
                'flip_h': bool(filtered_layer.get("flip_horizontal", False)),
                'flip_v': bool(filtered_layer.get("flip_vertical", False)),
            }
            
            if filtered_layer.get("is_gif", False):
                # Для GIF загружаем только метаданные
                self.image_cache.get_gif_frames(name, filepath, transform_params)
            else:
                # Для PNG загружаем сразу
                self.image_cache.get_image(name, filepath, transform_params)
        
        # Загружаем группы - исправляем здесь
        for group in model_json.get("groups", []):
            name = group.get("name")
            if name:
                # Фильтруем только нужные поля
                filtered_group = {}
                valid_fields = ["name", "children", "parent", "logic", 
                            "blink_freq", "random_effect", "random_min", "random_max"]
                
                for field in valid_fields:
                    if field in group:
                        filtered_group[field] = group[field]
                
                self.groups_by_name[name] = filtered_group
                
                # Инициализируем таймеры
                if name not in self.group_blink_timers:
                    self.group_blink_timers[name] = time.time() + random.uniform(2.0, 6.0)
                    self.group_blink_until[name] = 0.0
                
                if filtered_group.get("random_effect", False):
                    self.group_random_timers[name] = time.time()
                    self.group_random_current[name] = None
        
        # Инвалидируем кэш видимых слоев
        self._visible_layers_cache = []
        self._visible_cache_time = 0
        
        logger.info(f"Model loaded: {model_json.get('name', 'unnamed')}")
    
    def set_audio_level(self, level):
        if level < self.noise_gate:
            level = 0.0
        self.audio_level = max(0.0, float(level))
        
        if self.idle_enabled and level > self._get_min_active_threshold():
            self.last_activity_time = time.time()
    
    def _get_min_active_threshold(self):
        min_threshold = 1.0
        for state in ['whisper', 'normal', 'shout']:
            if self.active_states.get(state, True) and state in self.thresholds:
                threshold = self.thresholds[state]
                if threshold < min_threshold:
                    min_threshold = threshold
        return min_threshold if min_threshold < 1.0 else 0.0
    
    def get_frame_bytes(self):
        with self._lock:
            return self._frame_bytes
    
    def _get_visible_layers_cached(self):
        """Получает видимые слои с кэшированием"""
        current_time = time.time()
        
        # Если кэш актуален, возвращаем его
        if (self._visible_layers_cache and 
            current_time - self._visible_cache_time < self._cache_valid_for):
            return self._visible_layers_cache
        
        # Иначе пересчитываем
        visible_layers = self._calculate_visible_layers()
        self._visible_layers_cache = visible_layers
        self._visible_cache_time = current_time
        
        return visible_layers
    
    def _calculate_visible_layers(self):
        """Вычисляет видимые слои с учетом иерархии групп"""
        visible_layer_names = set()
        processed_groups = set()
        
        def process_group(group_name):
            if group_name in processed_groups:
                return
            processed_groups.add(group_name)
            
            group = self.groups_by_name.get(group_name)
            if not group:
                return
            
            chosen = self._choose_group_child(group)
            
            if not chosen:
                # Показываем все видимые слои группы
                for layer_name in self.layers_by_group.get(group_name, []):
                    layer = self.layers_by_name.get(layer_name)
                    if layer and layer.get("visible", True):
                        visible_layer_names.add(layer_name)
                return
            
            if chosen in self.groups_by_name:
                # Рекурсивно обрабатываем дочернюю группу
                process_group(chosen)
            else:
                # Добавляем выбранный слой
                if chosen in self.layers_by_name:
                    visible_layer_names.add(chosen)
        
        # Обрабатываем корневые группы
        root_groups = [name for name, g in self.groups_by_name.items() 
                      if not g.get("parent")]
        for group_name in root_groups:
            process_group(group_name)
        
        # Добавляем элементы без групп
        for layer in self.model.get("layers", []):
            if not layer.get("group") and layer.get("visible", True):
                visible_layer_names.add(layer.get("name"))
        
        # Сортируем по порядку в модели
        ordered_layers = []
        for layer in self.model.get("layers", []):
            name = layer.get("name")
            if name in visible_layer_names and layer.get("visible", True):
                ordered_layers.append(name)
        
        return ordered_layers
    
    def _choose_group_child(self, group):
        """Оптимизированный выбор дочернего элемента группы"""
        group_name = group.get("name")
        logic = group.get("logic", {})
        now = time.time()
        
        # Обработка моргания
        if self.effects.get('blink', True):
            blink_freq = float(group.get("blink_freq", 0.0))
            
            if group_name not in self.group_blink_timers:
                self.group_blink_timers[group_name] = now + random.uniform(2.0, 6.0)
                self.group_blink_until[group_name] = 0.0
            
            if blink_freq > 0.001:
                if now > self.group_blink_timers.get(group_name, 0):
                    self.group_blink_until[group_name] = now + 0.12
                    self.group_blink_timers[group_name] = now + blink_freq
                
                if now < self.group_blink_until.get(group_name, 0):
                    blink_target = logic.get("blink")
                    if blink_target:
                        return blink_target
        
        # Обработка случайного эффекта
        if group.get("random_effect", False) and self.effects.get('random_effect', False):
            min_time = group.get("random_min", 5.0)
            max_time = group.get("random_max", 10.0)
            
            if group_name not in self.group_random_timers:
                self.group_random_timers[group_name] = now
                self.group_random_current[group_name] = None
            
            if now > self.group_random_timers.get(group_name, 0):
                children = group.get("children", [])
                if children:
                    blink_layer = logic.get("blink", "")
                    open_layer = logic.get("open", "")
                    
                    available = []
                    for child_name in children:
                        if child_name in self.layers_by_name:
                            if child_name != blink_layer and child_name != open_layer:
                                available.append(child_name)
                        elif child_name in self.groups_by_name:
                            child_group = self.groups_by_name.get(child_name)
                            if child_group and child_group.get("random_effect", False):
                                available.append(child_name)
                    
                    if available:
                        chosen = random.choice(available)
                        self.group_random_current[group_name] = chosen
                        interval = random.uniform(min_time, max_time)
                        self.group_random_timers[group_name] = now + interval
            
            if self.group_random_current.get(group_name):
                return self.group_random_current.get(group_name)
        
        # Определение текущего состояния на основе звука
        current_state = "silent"
        
        if self.audio_level > self.thresholds['shout'] and self.active_states.get('shout', True):
            current_state = "shout"
        elif self.audio_level > self.thresholds['normal'] and self.active_states.get('normal', True):
            current_state = "normal"
        elif self.audio_level > self.thresholds['whisper'] and self.active_states.get('whisper', True):
            current_state = "whisper"
        elif self.audio_level > self.thresholds['silent'] and self.active_states.get('silent', True):
            current_state = "silent"
        
        # Возвращаем целевой слой/группу
        if current_state in logic and self.active_states.get(current_state, True):
            return logic[current_state]
        elif "open" in logic and logic["open"]:
            return logic["open"]
        elif "normal" in logic and logic["normal"]:
            return logic["normal"]
        elif "silent" in logic and logic["silent"]:
            return logic["silent"]
        
        # Возвращаем первый доступный слой
        for child_name in group.get("children", []):
            if child_name in self.layers_by_name:
                return child_name
        
        return None
    
    def _get_layer_image(self, layer_name: str) -> Optional[Image.Image]:
        """Быстрое получение изображения слоя с кэшированием"""
        layer = self.layers_by_name.get(layer_name)
        if not layer:
            return None
        
        filename = layer.get("file")
        if not filename:
            return None
        
        filepath = os.path.join(self.model_dir, filename)
        
        # Параметры трансформации
        transform_params = {
            'scale': float(layer.get("scale", 1.0)),
            'rotation': int(layer.get("rotation", 0)),
            'flip_h': bool(layer.get("flip_horizontal", False)),
            'flip_v': bool(layer.get("flip_vertical", False)),
        }
        
        if layer.get("is_gif", False):
            # Обработка GIF
            gif_data = self.image_cache.get_gif_frames(layer_name, filepath, transform_params)
            if not gif_data:
                return None
            
            frames, durations = gif_data
            if not frames:
                return None
            
            # Обновляем текущий кадр
            now = time.time()
            if layer_name not in self._gif_current_frame:
                self._gif_current_frame[layer_name] = 0
                self._gif_last_update[layer_name] = now
            
            current_frame = self._gif_current_frame[layer_name]
            
            if now - self._gif_last_update[layer_name] > durations[current_frame]:
                self._gif_current_frame[layer_name] = (current_frame + 1) % len(frames)
                self._gif_last_update[layer_name] = now
                current_frame = self._gif_current_frame[layer_name]
            
            return frames[current_frame]
        else:
            # Обработка статического изображения
            return self.image_cache.get_image(layer_name, filepath, transform_params)
    
    def _optimized_render_frame(self) -> Image.Image:
        """Оптимизированная отрисовка кадра"""
        # Очищаем буфер
        self.frame_buffer.clear()
        base = self.frame_buffer.get_image()
        
        if not self.model or not self.model_dir:
            return base
        
        # Получаем видимые слои
        visible_layers = self._get_visible_layers_cached()
        
        # Предварительно вычисляем смещения для эффектов
        offset_x, offset_y = 0, 0
        bounce_intensity = 0
        pulse_scale = 1.0
        
        if self.effects.get('bounce', False):
            bounce_intensity = int(math.sin(time.time() * 5) * min(10, self.audio_level * 20))
        
        if self.effects.get('shake', False):
            shake_intensity = min(1.0, self.audio_level * 5)
            offset_x = int((random.random() - 0.5) * 10 * shake_intensity)
            offset_y = int((random.random() - 0.5) * 10 * shake_intensity) + bounce_intensity
        else:
            offset_y = bounce_intensity
        
        if self.effects.get('pulse', False):
            pulse_scale = 1.0 + (math.sin(time.time() * 5) * 0.1 * self.audio_level)
        
        # Отрисовываем слои
        for layer_name in visible_layers:
            image = self._get_layer_image(layer_name)
            if not image:
                continue
            
            layer = self.layers_by_name.get(layer_name)
            if not layer:
                continue
            
            # Применяем пульсацию
            if pulse_scale != 1.0:
                new_w = int(image.width * pulse_scale)
                new_h = int(image.height * pulse_scale)
                if new_w > 0 and new_h > 0:
                    image = image.resize((new_w, new_h), Image.LANCZOS)
            
            # Позиционируем
            px = (self.width - image.width) // 2 + int(layer.get("x", 0)) + offset_x
            py = (self.height - image.height) // 2 + int(layer.get("y", 0)) + offset_y
            
            # Композиция
            try:
                base.alpha_composite(image, (px, py))
            except Exception as e:
                logger.error(f"Ошибка композиции {layer_name}: {e}")
        
        # Применяем idle-режим
        if self.idle_enabled:
            current_time = time.time()
            if current_time - self.last_activity_time > self.idle_timeout:
                enhancer = ImageEnhance.Brightness(base)
                base = enhancer.enhance(self.idle_brightness)
        
        return base
    
    def _optimized_loop(self):
        """Оптимизированный главный цикл рендеринга"""
        frame_count = 0
        last_stats_time = time.time()
        frame_times = deque(maxlen=60)  # Буфер для 60 последних фреймов
        
        while self._running:
            frame_start = time.time()
            
            try:
                # Рендерим кадр
                frame_image = self._optimized_render_frame()
                
                # Конвертируем в байты
                with io.BytesIO() as buf:
                    frame_image.save(buf, format="PNG", optimize=True, compress_level=1)
                    data = buf.getvalue()
                
                # Сохраняем кадр
                with self._lock:
                    self._frame_bytes = data
                
                frame_count += 1
                
                # Вычисляем время кадра
                frame_time = time.time() - frame_start
                frame_times.append(frame_time)
                
                # Логируем статистику каждую секунду
                current_time = time.time()
                if current_time - last_stats_time >= 1.0:
                    avg_frame_time = sum(frame_times) / len(frame_times) if frame_times else 0
                    fps = 1.0 / avg_frame_time if avg_frame_time > 0 else 0
                    
                    logger.debug(f"FPS: {fps:.1f}, Avg frame time: {avg_frame_time*1000:.1f}ms")
                    last_stats_time = current_time
                
                # Спим для поддержания FPS
                elapsed = time.time() - frame_start
                sleep_time = self.target_frame_time - elapsed
                
                if sleep_time > 0:
                    # Минимальный сон 1мс для избежания busy wait
                    time.sleep(max(0.001, sleep_time))
                elif elapsed > self.target_frame_time * 1.5:
                    # Предупреждение о низкой производительности
                    logger.warning(f"Frame took too long: {elapsed*1000:.1f}ms")
                    
            except Exception as e:
                logger.error(f"Error in render loop: {e}")
                import traceback
                logger.error(traceback.format_exc())
                time.sleep(0.016)  # Спим на 1 кадр при ошибке