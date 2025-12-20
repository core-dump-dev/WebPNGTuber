import threading, time
from PIL import Image, ImageEnhance, ImageSequence
import os, io, math, random
import logging
import logging.handlers
from datetime import datetime
from functools import lru_cache

# Определение базовой директории
import sys
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
    # Форматирование
    formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    # Файловый обработчик с ротацией
    log_file = os.path.join(LOGS_DIR, 'renderer.log')
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
        self.group_blink_timers = {}
        self.group_blink_until = {}
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
        self.state_order = ['silent', 'whisper', 'normal', 'shout']
        self.effects = {}
        self.group_random_timers = {}
        self.group_random_current = {}
        self.idle_enabled = False
        self.idle_timeout = 60.0
        self.last_activity_time = time.time()
        self.idle_brightness = 0.5
        
        # Оптимизации
        self._image_cache = {}  # Кэш загруженных изображений
        self._transformed_cache = {}  # Кэш трансформированных изображений
        self._last_frame_time = 0
        self._frame_interval = 1.0 / fps
        
        # Кэш для GIF
        self._gif_frames = {}
        self._gif_frame_times = {}
        self._gif_current_frame = {}
        self._gif_last_update = {}
        
        # Кэш для видимых слоев
        self._visible_layers_cache = []
        self._visible_layers_cache_time = 0
        self._cache_ttl = 0.1  # 100 мс
        
        logger.info("Renderer initialized with optimizations")
    
    def set_idle(self, enabled, timeout):
        self.idle_enabled = enabled
        self.idle_timeout = timeout
        self.last_activity_time = time.time()
        logger.info(f"Idle mode set: enabled={enabled}, timeout={timeout}")
    
    def set_noise_gate(self, threshold):
        self.noise_gate = threshold
        logger.info(f"Noise gate threshold set: {threshold}")
    
    def set_effects(self, effects):
        self.effects = effects
        logger.info(f"Effects set: {effects}")
    
    def set_thresholds(self, thresholds):
        self.thresholds = thresholds
        logger.info(f"Thresholds set: {thresholds}")
    
    def set_active_states(self, active_states):
        self.active_states = active_states
        logger.info(f"Active states set: {active_states}")
    
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
    
    def load_model(self, model_json, model_dir):
        self.model = model_json
        self.model_dir = model_dir
        
        self.width = model_json.get('width', 700)
        self.height = model_json.get('height', 700)
        
        self._image_cache.clear()
        self._transformed_cache.clear()
        self._gif_frames.clear()
        self._gif_frame_times.clear()
        self._gif_current_frame.clear()
        self._gif_last_update.clear()
        
        self.layers_by_name = {l.get('name'): l for l in self.model.get('layers', [])}
        self.groups_by_name = {g.get('name'): g for g in self.model.get('groups', [])}
        
        # Предзагрузка и кэширование изображений
        for layer in self.model.get("layers", []):
            filename = layer.get("file")
            if not filename:
                continue
            
            file_path = os.path.join(self.model_dir, filename)
            if not os.path.exists(file_path):
                continue
            
            try:
                # Загружаем оригинальное изображение БЕЗ конвертации
                img = Image.open(file_path)
                
                # Проверяем, является ли изображение анимированным GIF
                is_animated = getattr(img, 'is_animated', False) and img.format == 'GIF'
                
                # Получаем параметры трансформации
                scale = float(layer.get("scale", 1.0))
                rotation = int(layer.get("rotation", 0))
                flip_h = bool(layer.get("flip_horizontal", False))
                flip_v = bool(layer.get("flip_vertical", False))
                
                # Для GIF - сохраняем оригинал и все кадры
                if is_animated:
                    # Сохраняем оригинальное GIF изображение
                    self._image_cache[layer.get("name")] = img.copy()
                    
                    # Извлекаем все кадры GIF
                    frames = []
                    frame_times = []
                    
                    # Сбрасываем к началу
                    img.seek(0)
                    
                    try:
                        while True:
                            # Копируем текущий кадр и конвертируем в RGBA
                            frame = img.copy().convert("RGBA")
                            
                            # Применяем масштаб ПЕРВЫМ
                            if scale != 1.0:
                                new_width = max(1, int(frame.width * scale))
                                new_height = max(1, int(frame.height * scale))
                                frame = frame.resize((new_width, new_height), Image.LANCZOS)
                            
                            # Затем отражение
                            if flip_h:
                                frame = frame.transpose(Image.FLIP_LEFT_RIGHT)
                            if flip_v:
                                frame = frame.transpose(Image.FLIP_TOP_BOTTOM)
                            
                            # Затем поворот
                            if rotation != 0:
                                frame = frame.rotate(rotation, expand=True, resample=Image.BICUBIC)
                            
                            frames.append(frame)
                            
                            # Получаем длительность кадра
                            try:
                                duration = img.info.get('duration', 100)
                                frame_times.append(duration / 1000.0)  # в секундах
                            except:
                                frame_times.append(0.1)
                            
                            # Переходим к следующему кадру
                            img.seek(img.tell() + 1)
                    except EOFError:
                        pass
                    
                    # Сохраняем кадры GIF
                    self._gif_frames[layer.get("name")] = frames
                    self._gif_frame_times[layer.get("name")] = frame_times
                    self._gif_current_frame[layer.get("name")] = 0
                    self._gif_last_update[layer.get("name")] = 0
                    
                else:
                    # Для статичных изображений конвертируем в RGBA
                    img = img.convert("RGBA")
                    
                    # Ограничиваем размер для производительности
                    if img.width > 2048 or img.height > 2048:
                        img.thumbnail((2048, 2048), Image.LANCZOS)
                    
                    transformed = img.copy()
                    
                    # Применяем масштаб ПЕРВЫМ
                    if scale != 1.0:
                        new_width = max(1, int(transformed.width * scale))
                        new_height = max(1, int(transformed.height * scale))
                        transformed = transformed.resize((new_width, new_height), Image.LANCZOS)
                    
                    # Затем отражение
                    if flip_h:
                        transformed = transformed.transpose(Image.FLIP_LEFT_RIGHT)
                    if flip_v:
                        transformed = transformed.transpose(Image.FLIP_TOP_BOTTOM)
                    
                    # Затем поворот
                    if rotation != 0:
                        transformed = transformed.rotate(rotation, expand=True, resample=Image.BICUBIC)
                    
                    # Кэшируем трансформированное изображение
                    cache_key = f"{layer.get('name')}_{scale}_{rotation}_{flip_h}_{flip_v}"
                    self._transformed_cache[cache_key] = transformed
                    
                    # Сохраняем в основной кэш
                    self._image_cache[layer.get("name")] = transformed
                    
            except Exception as e:
                logger.error(f"Ошибка загрузки изображения {filename}: {e}")
                # Создаем placeholder для пропущенного изображения
                placeholder = Image.new("RGBA", (100, 100), (255, 0, 0, 128))
                self._image_cache[layer.get("name")] = placeholder
        
        # Инициализация таймеров групп
        for g in self.model.get("groups", []):
            name = g.get("name")
            if name not in self.group_blink_timers:
                self.group_blink_timers[name] = time.time() + random.uniform(2.0, 6.0)
                self.group_blink_until[name] = 0.0
            
            if g.get("random_effect", False):
                self.group_random_timers[name] = time.time()
                self.group_random_current[name] = None
            
            if g.get("blink_freq", 0.0) > 0.0:
                self.group_blink_timers[name] = time.time() + random.uniform(0.5, 2.0)
                self.group_blink_until[name] = 0.0
        
        logger.info(f"Model loaded: {model_json.get('name', 'unnamed')} with size {self.width}x{self.height}")
        
        # Инициализация таймеров групп
        for g in self.model.get("groups", []):
            name = g.get("name")
            if name not in self.group_blink_timers:
                self.group_blink_timers[name] = time.time() + random.uniform(2.0, 6.0)
                self.group_blink_until[name] = 0.0
            
            if g.get("random_effect", False):
                self.group_random_timers[name] = time.time()
                self.group_random_current[name] = None
            
            if g.get("blink_freq", 0.0) > 0.0:
                self.group_blink_timers[name] = time.time() + random.uniform(0.5, 2.0)
                self.group_blink_until[name] = 0.0
        
        logger.info(f"Model loaded: {model_json.get('name', 'unnamed')} with size {self.width}x{self.height}")
    
    def set_audio_level(self, level):
        # Быстрая реакция на изменения уровня без задержек
        if level < self.noise_gate:
            level = 0.0
        self.audio_level = max(0.0, float(level))
        # Обновляем время активности только если звук выше минимального активного порога
        if self.idle_enabled and level > self._get_min_active_threshold():
            self.last_activity_time = time.time()
        # Инвалидируем кэш при изменении уровня для быстрой реакции
        self._visible_layers_cache_time = 0
    
    def _get_min_active_threshold(self):
        """Получает минимальный порог из активных голосовых состояний (исключая тишину)"""
        voice_states = ['whisper', 'normal', 'shout']
        min_threshold = 1.0  # Максимальное значение по умолчанию
        
        for state in voice_states:
            if self.active_states.get(state, True) and state in self.thresholds:
                threshold = self.thresholds[state]
                if threshold < min_threshold:
                    min_threshold = threshold
                    
        return min_threshold if min_threshold < 1.0 else 0.0
    
    def get_frame_bytes(self):
        with self._lock:
            return self._frame_bytes
    
    def _get_all_child_groups(self, group_name):
        """Рекурсивно получает все дочерние группы для указанной группы"""
        children = []
        for g_name, group in self.groups_by_name.items():
            if group.get("parent") == group_name:
                children.append(g_name)
                children.extend(self._get_all_child_groups(g_name))
        return children
    
    def _get_visible_layers(self):
        """Возвращает список видимых слоев с учетом иерархии групп и порядка в model["layers"]"""
        # Кэширование видимых слоев для оптимизации
        now = time.time()
        if now - self._visible_layers_cache_time < self._cache_ttl and self._visible_layers_cache:
            return self._visible_layers_cache
        
        # Сначала собираем все слои, которые должны быть видны согласно логике групп
        visible_layer_names = set()
        processed_groups = set()
        
        # Функция для рекурсивной обработки групп
        def process_group(group_name):
            if group_name in processed_groups:
                return
            processed_groups.add(group_name)
            
            # Получаем группу
            group = self.groups_by_name.get(group_name)
            if not group:
                return
                
            # Получаем текущее состояние для группы
            chosen = self._choose_group_child(group)
            
            # Если состояние не определено, показываем все видимые слои группы
            if not chosen:
                for layer in self.model.get("layers", []):
                    if layer.get("group") == group_name and layer.get("visible", True):
                        layer_name = layer.get("name")
                        if layer_name:
                            visible_layer_names.add(layer_name)
                return
                
            # Проверяем, является ли chosen группой или слоем
            if chosen in self.groups_by_name:
                # Если это группа - рекурсивно обрабатываем ее
                process_group(chosen)
            else:
                # Если это слой - добавляем его в видимые
                if chosen and chosen in self.layers_by_name:
                    visible_layer_names.add(chosen)
        
        # Обрабатываем корневые группы (без родителя)
        root_groups = [name for name, g in self.groups_by_name.items() if not g.get("parent")]
        for group_name in root_groups:
            process_group(group_name)
            
        # Добавляем элементы без групп
        for layer in self.model.get("layers", []):
            if not layer.get("group") and layer.get("visible", True):
                layer_name = layer.get("name")
                if layer_name:
                    visible_layer_names.add(layer_name)
        
        # Сортируем согласно порядку в model["layers"]
        ordered_visible_layers = []
        for layer in self.model.get("layers", []):
            layer_name = layer.get("name")
            if layer_name in visible_layer_names and layer.get("visible", True):
                ordered_visible_layers.append(layer_name)
        
        # Обновляем кэш
        self._visible_layers_cache = ordered_visible_layers
        self._visible_layers_cache_time = now
        
        return ordered_visible_layers
    
    def _resolve_to_layer(self, name, group_name, visited=None):
        if visited is None:
            visited = set()
        if name in visited:
            return None
        visited.add(name)
        if name in self.layers_by_name:
            return name
        if name in self.groups_by_name:
            group = self.groups_by_name[name]
            chosen = self._choose_group_child(group)
            if chosen:
                return self._resolve_to_layer(chosen, group_name, visited)
        return None
    
    def _choose_group_child(self, group):
        """Выбирает дочерный элемент группы в зависимости от текущего состояния - оптимизировано"""
        group_name = group.get("name")
        logic = group.get("logic", {})
        now = time.time()
        
        # Обработка моргания
        if self.effects.get('blink', True):
            blink_freq = float(group.get("blink_freq", 0.0))
            
            # Инициализация таймеров для группы, если их нет
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
                    else:
                        # Автоматический поиск слоя для моргания
                        for child_name in group.get("children", []):
                            child = self.layers_by_name.get(child_name) or self.groups_by_name.get(child_name)
                            if child and any(kw in child_name.lower() for kw in ["close", "closed", "shut", "blink", "морг", "закр"]):
                                return child_name
        
        # Обработка случайного эффекта
        if group.get("random_effect", False) and self.effects.get('random_effect', False):
            min_time = group.get("random_min", 5.0)
            max_time = group.get("random_max", 10.0)
            
            # Инициализация таймеров для случайного эффекта
            if group_name not in self.group_random_timers:
                self.group_random_timers[group_name] = now
                self.group_random_current[group_name] = None
                
            if now > self.group_random_timers.get(group_name, 0):
                children = group.get("children", [])
                if children:
                    blink_layer = logic.get("blink", "")
                    open_layer = logic.get("open", "")
                    # Фильтруем только слои (не группы) для случайного выбора
                    available = []
                    for child_name in children:
                        # Проверяем, является ли ребенок слоем или группой
                        if child_name in self.layers_by_name:
                            # Это слой
                            if child_name != blink_layer and child_name != open_layer:
                                available.append(child_name)
                        else:
                            # Это группа, проверяем есть ли в ней видимые слои
                            child_group = self.groups_by_name.get(child_name)
                            if child_group and child_group.get("random_effect", False):
                                available.append(child_name)
                    
                    if available:
                        chosen = random.choice(available)
                        self.group_random_current[group_name] = chosen
                        # Устанавливаем новый таймер
                        interval = random.uniform(min_time, max_time)
                        self.group_random_timers[group_name] = now + interval
            
            # Возвращаем текущий случайный выбор, если он есть
            if self.group_random_current.get(group_name):
                return self.group_random_current.get(group_name)
        
        # Определение текущего состояния на основе уровня звука - быстрая проверка
        current_state = "silent"
        
        # Проверяем состояния в порядке убывания громкости для быстрой реакции
        if self.audio_level > self.thresholds['shout'] and self.active_states.get('shout', True):
            current_state = "shout"
        elif self.audio_level > self.thresholds['normal'] and self.active_states.get('normal', True):
            current_state = "normal"
        elif self.audio_level > self.thresholds['whisper'] and self.active_states.get('whisper', True):
            current_state = "whisper"
        elif self.audio_level > self.thresholds['silent'] and self.active_states.get('silent', True):
            current_state = "silent"
        
        # Fallback-логика для выбора состояния
        if current_state in logic and self.active_states.get(current_state, True):
            return logic[current_state]
        elif "open" in logic and logic["open"]:
            return logic["open"]
        elif "normal" in logic and logic["normal"]:
            return logic["normal"]
        elif "silent" in logic and logic["silent"]:
            return logic["silent"]
            
        # Если ничего не подошло, пробуем найти первый доступный слой
        for child_name in group.get("children", []):
            if child_name in self.layers_by_name:
                return child_name
                
        return None
    
    def _get_layer_image(self, layer_name):
        """Оптимизированное получение изображения слоя с кэшированием GIF"""
        if layer_name in self._gif_frames:
            now = time.time()
            frames = self._gif_frames[layer_name]
            frame_times = self._gif_frame_times[layer_name]
            
            if layer_name not in self._gif_last_update:
                self._gif_last_update[layer_name] = now
                self._gif_current_frame[layer_name] = 0
                
            current_frame = self._gif_current_frame[layer_name]
            
            # Оптимизированная обработка GIF - обновляем только при необходимости
            if now - self._gif_last_update[layer_name] > frame_times[current_frame]:
                self._gif_current_frame[layer_name] = (current_frame + 1) % len(frames)
                self._gif_last_update[layer_name] = now
                
            return frames[self._gif_current_frame[layer_name]]
            
        elif layer_name in self._image_cache:
            return self._image_cache[layer_name]
            
        return None
    
    def _loop(self):
        """Основной цикл рендеринга с оптимизациями"""
        while self._running:
            frame_start = time.time()
            
            # Создаем базовое изображение
            frame_image = Image.new("RGBA", (self.width, self.height), (0, 0, 0, 0))
            
            if self.model and self.model_dir:
                # Получаем видимые слои
                visible_layers = self._get_visible_layers()
                
                # Отрисовываем видимые слои в правильном порядке
                for layer_name in visible_layers:
                    image = self._get_layer_image(layer_name)
                    if not image:
                        continue
                    
                    layer = self.layers_by_name.get(layer_name)
                    if not layer:
                        continue
                    
                    # Сохраняем оригинал для эффектов
                    orig_image = image
                    
                    # ПРИМЕНЯЕМ ЭФФЕКТЫ ТУТ
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
                        new_size = (int(image.width * pulse_scale), int(image.height * pulse_scale))
                        image = image.resize(new_size, Image.LANCZOS)
                    
                    # Позиционирование (центрируем изображение) с учетом эффектов
                    px = (self.width - image.width) // 2 + int(layer.get("x", 0)) + offset_x
                    py = (self.height - image.height) // 2 + int(layer.get("y", 0)) + offset_y
                    
                    try:
                        frame_image.alpha_composite(image, (px, py))
                    except Exception as e:
                        logger.error(f"Ошибка композиции слоя {layer_name}: {e}")
            
            # Применяем idle-режим
            if self.idle_enabled:
                current_time = time.time()
                if current_time - self.last_activity_time > self.idle_timeout:
                    enhancer = ImageEnhance.Brightness(frame_image)
                    frame_image = enhancer.enhance(self.idle_brightness)
            
            # Сохраняем кадр
            with io.BytesIO() as buf:
                frame_image.save(buf, format="PNG", optimize=True, compress_level=1)
                data = buf.getvalue()
            
            with self._lock:
                self._frame_bytes = data
            
            # Контроль FPS
            elapsed = time.time() - frame_start
            sleep_time = self._frame_interval - elapsed
            
            if sleep_time > 0:
                time.sleep(sleep_time)
            elif elapsed > self._frame_interval * 1.5:
                logger.warning(f"Frame render took {elapsed*1000:.2f}ms, target: {self._frame_interval*1000:.2f}ms")