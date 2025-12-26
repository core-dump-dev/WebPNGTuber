import threading, time
from PIL import Image, ImageEnhance, ImageSequence
import os, io, math, random
import logging
import logging.handlers
import sys

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
        self._image_cache = {}
        self._transformed_cache = {}
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
        self._cache_ttl = 0.1
        
        self.layers_by_name = {}
        self.groups_by_name = {}
        
        # Кэш рендера
        self._render_cache = None
        self._render_cache_time = 0
        self._render_cache_duration = 0.05  # 50ms кэш
        
        # Эффект волны/искажения (переименовано с distortion)
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
        
        logger.info(f"Renderer initialized (CPU only) with optimizations")
    
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
        
        # Очищаем кэши
        self._image_cache.clear()
        self._transformed_cache.clear()
        self._gif_frames.clear()
        self._gif_frame_times.clear()
        self._gif_current_frame.clear()
        self._gif_last_update.clear()
        self._wave_frames_cache.clear()
        
        # Загружаем слои с уникальными именами
        self.layers_by_name = {}
        for idx, layer in enumerate(self.model.get('layers', [])):
            layer_name = layer.get('name')
            if not layer_name:
                continue
                
            unique_name = layer_name
            counter = 1
            while unique_name in self.layers_by_name:
                unique_name = f"{layer_name}_{counter}"
                counter += 1
                
            layer_copy = layer.copy()
            layer_copy['_unique_name'] = unique_name
            layer_copy['_original_index'] = idx
            layer_copy['alpha'] = float(layer.get('alpha', 1.0))
            self.layers_by_name[unique_name] = layer_copy
        
        self.groups_by_name = {g.get('name'): g for g in self.model.get('groups', [])}
        
        # Предзагрузка и кэширование изображений
        for idx, layer in enumerate(self.model.get("layers", [])):
            filename = layer.get("file")
            if not filename:
                continue
            
            file_path = os.path.join(self.model_dir, filename)
            if not os.path.exists(file_path):
                continue
            
            try:
                # Находим уникальное имя для этого слоя
                layer_name = layer.get("name")
                unique_name = None
                for uname, ldata in self.layers_by_name.items():
                    if ldata.get('name') == layer_name and ldata.get('_original_index') == idx:
                        unique_name = uname
                        break
                
                if not unique_name:
                    logger.warning(f"Не найден уникальный ключ для слоя {layer_name}, индекс {idx}")
                    continue
                    
                # Загружаем изображение
                img = Image.open(file_path)
                
                # Проверяем, является ли изображение анимированным GIF
                is_animated = getattr(img, 'is_animated', False) and img.format == 'GIF'
                
                # Получаем параметры трансформации
                scale = float(layer.get("scale", 1.0))
                rotation = int(layer.get("rotation", 0))
                flip_h = bool(layer.get("flip_horizontal", False))
                flip_v = bool(layer.get("flip_vertical", False))
                
                # Для GIF
                if is_animated:
                    # Извлекаем все кадры GIF
                    frames = []
                    frame_times = []
                    
                    img.seek(0)
                    try:
                        while True:
                            # Копируем текущий кадр
                            frame = img.copy()
                            
                            # Конвертируем в RGBA
                            frame = frame.convert("RGBA")
                            
                            # Применяем трансформации
                            if scale != 1.0:
                                new_width = max(1, int(frame.width * scale))
                                new_height = max(1, int(frame.height * scale))
                                frame = frame.resize((new_width, new_height), Image.Resampling.LANCZOS)
                            
                            if flip_h:
                                frame = frame.transpose(Image.FLIP_LEFT_RIGHT)
                            if flip_v:
                                frame = frame.transpose(Image.FLIP_TOP_BOTTOM)
                            
                            if rotation != 0:
                                frame = frame.rotate(rotation, expand=True, resample=Image.Resampling.BICUBIC)
                            
                            frames.append(frame)
                            
                            # Длительность кадра
                            try:
                                duration = img.info.get('duration', 100)
                                frame_times.append(duration / 1000.0)
                            except:
                                frame_times.append(0.1)
                            
                            img.seek(img.tell() + 1)
                    except EOFError:
                        pass
                    
                    # Сохраняем кадры GIF
                    self._gif_frames[unique_name] = frames
                    self._gif_frame_times[unique_name] = frame_times
                    self._gif_current_frame[unique_name] = 0
                    self._gif_last_update[unique_name] = time.time()
                    
                    # Первый кадр для статичного отображения
                    self._image_cache[unique_name] = frames[0] if frames else None
                    
                else:
                    # Для статичных изображений
                    img = img.convert("RGBA")
                    
                    # Ограничиваем размер для производительности
                    if img.width > 2048 or img.height > 2048:
                        img.thumbnail((2048, 2048), Image.Resampling.LANCZOS)
                    
                    transformed = img.copy()
                    
                    # Применяем трансформации
                    if scale != 1.0:
                        new_width = max(1, int(transformed.width * scale))
                        new_height = max(1, int(transformed.height * scale))
                        transformed = transformed.resize((new_width, new_height), Image.Resampling.LANCZOS)
                    
                    if flip_h:
                        transformed = transformed.transpose(Image.FLIP_LEFT_RIGHT)
                    if flip_v:
                        transformed = transformed.transpose(Image.FLIP_TOP_BOTTOM)
                    
                    if rotation != 0:
                        transformed = transformed.rotate(rotation, expand=True, resample=Image.Resampling.BICUBIC)
                    
                    # Кэшируем
                    cache_key = f"{unique_name}_{scale}_{rotation}_{flip_h}_{flip_v}"
                    self._transformed_cache[cache_key] = transformed
                    self._image_cache[unique_name] = transformed
                    
            except Exception as e:
                logger.error(f"Ошибка загрузки изображения {filename}: {e}")
                placeholder = Image.new("RGBA", (100, 100), (255, 0, 0, 128))
                self._image_cache[unique_name] = placeholder
        
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
        
        # Предрассчитываем кадры эффекта "Волна" только для статичных изображений
        if self.wave_enabled:
            self._precalculate_wave_frames()
        
        logger.info(f"Model loaded: {model_json.get('name', 'unnamed')} with size {self.width}x{self.height}")
    
    def set_audio_level(self, level):
        if level < self.noise_gate:
            level = 0.0
        self.audio_level = max(0.0, float(level))
        if self.idle_enabled and level > self._get_min_active_threshold():
            self.last_activity_time = time.time()
        self._visible_layers_cache_time = 0
    
    def _get_min_active_threshold(self):
        voice_states = ['whisper', 'normal', 'shout']
        min_threshold = 1.0
        
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
        children = []
        for g_name, group in self.groups_by_name.items():
            if group.get("parent") == group_name:
                children.append(g_name)
                children.extend(self._get_all_child_groups(g_name))
        return children
    
    def _get_visible_layers(self):
        if not self.model:
            return []
        
        now = time.time()
        if now - self._visible_layers_cache_time < self._cache_ttl and self._visible_layers_cache:
            return self._visible_layers_cache
        
        name_to_unique = {}
        for unique_name, layer_data in self.layers_by_name.items():
            orig_name = layer_data.get('name')
            if orig_name:
                if orig_name not in name_to_unique:
                    name_to_unique[orig_name] = []
                name_to_unique[orig_name].append(unique_name)
        
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
                for layer in self.model.get('layers', []):
                    if layer.get('group') == group_name and layer.get('visible', True):
                        layer_name = layer.get('name')
                        if layer_name and layer_name in name_to_unique:
                            for unique_name in name_to_unique[layer_name]:
                                layer_data = self.layers_by_name[unique_name]
                                if layer_data.get('_original_index') == self.model.get('layers', []).index(layer):
                                    visible_layer_names.add(unique_name)
                return
                
            if chosen in self.groups_by_name:
                process_group(chosen)
            else:
                if chosen and chosen in name_to_unique:
                    for unique_name in name_to_unique[chosen]:
                        visible_layer_names.add(unique_name)
        
        root_groups = [name for name, g in self.groups_by_name.items() if not g.get('parent')]
        for group_name in root_groups:
            process_group(group_name)
            
        for layer in self.model.get('layers', []):
            if not layer.get('group') and layer.get('visible', True):
                layer_name = layer.get('name')
                if layer_name and layer_name in name_to_unique:
                    for unique_name in name_to_unique[layer_name]:
                        layer_data = self.layers_by_name[unique_name]
                        if layer_data.get('_original_index') == self.model.get('layers', []).index(layer):
                            visible_layer_names.add(unique_name)
        
        ordered_visible_layers = []
        for layer in self.model.get('layers', []):
            layer_name = layer.get('name')
            if layer_name and layer_name in name_to_unique:
                for unique_name in name_to_unique[layer_name]:
                    layer_data = self.layers_by_name[unique_name]
                    if (layer_data.get('_original_index') == self.model.get('layers', []).index(layer) and
                        unique_name in visible_layer_names):
                        ordered_visible_layers.append(unique_name)
        
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
        group_name = group.get("name")
        logic = group.get("logic", {})
        now = time.time()
        
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
                    else:
                        for child_name in group.get("children", []):
                            child = self.layers_by_name.get(child_name) or self.groups_by_name.get(child_name)
                            if child and any(kw in child_name.lower() for kw in ["close", "closed", "shut", "blink", "морг", "закр"]):
                                return child_name
        
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
                        else:
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
        
        current_state = "silent"
        
        if self.audio_level > self.thresholds['shout'] and self.active_states.get('shout', True):
            current_state = "shout"
        elif self.audio_level > self.thresholds['normal'] and self.active_states.get('normal', True):
            current_state = "normal"
        elif self.audio_level > self.thresholds['whisper'] and self.active_states.get('whisper', True):
            current_state = "whisper"
        elif self.audio_level > self.thresholds['silent'] and self.active_states.get('silent', True):
            current_state = "silent"
        
        if current_state in logic and self.active_states.get(current_state, True):
            return logic[current_state]
        elif "open" in logic and logic["open"]:
            return logic["open"]
        elif "normal" in logic and logic["normal"]:
            return logic["normal"]
        elif "silent" in logic and logic["silent"]:
            return logic["silent"]
            
        for child_name in group.get("children", []):
            if child_name in self.layers_by_name:
                return child_name
                
        return None
    
    def _get_layer_image(self, layer_name):
        if layer_name in self._gif_frames:
            now = time.time()
            frames = self._gif_frames[layer_name]
            frame_times = self._gif_frame_times[layer_name]
            
            if layer_name not in self._gif_last_update:
                self._gif_last_update[layer_name] = now
                self._gif_current_frame[layer_name] = 0
                
            current_frame = self._gif_current_frame[layer_name]
            
            if now - self._gif_last_update[layer_name] > frame_times[current_frame]:
                self._gif_current_frame[layer_name] = (current_frame + 1) % len(frames)
                self._gif_last_update[layer_name] = now
                
            return frames[self._gif_current_frame[layer_name]]
            
        elif layer_name in self._image_cache:
            return self._image_cache[layer_name]
            
        return None
    
    def _create_wave_effect(self, image, frame_index):
        """Создает эффект волны/искажения"""
        if not image or image.width == 0 or image.height == 0:
            return image
        
        try:
            import numpy as np
            
            # Конвертируем в numpy array
            img_array = np.array(image)
            height, width = img_array.shape[:2]
            
            # Создаем сетку координат
            x, y = np.meshgrid(np.arange(width), np.arange(height))
            
            # Параметры искажения для этого кадра
            phase = (frame_index / 4.0) * 2 * np.pi
            time_offset = self._wave_last_update * self.wave_speed
            
            # Волновые искажения (несколько частот для более естественного вида)
            wave1 = np.sin(x * self.wave_frequency * 0.01 + time_offset + phase) * self.wave_amplitude
            wave2 = np.cos(y * self.wave_frequency * 0.008 + time_offset * 1.3 + phase) * self.wave_amplitude * 0.7
            wave3 = np.sin((x + y) * self.wave_frequency * 0.005 + time_offset * 0.7 + phase) * self.wave_amplitude * 0.5
            
            # Общее смещение
            dx = wave1 + wave3
            dy = wave2 + wave3 * 0.8
            
            # Нормализуем смещения
            dx = np.clip(dx, -self.wave_amplitude, self.wave_amplitude)
            dy = np.clip(dy, -self.wave_amplitude, self.wave_amplitude)
            
            # Создаем новые координаты
            new_x = np.clip(x + dx, 0, width - 1).astype(np.int32)
            new_y = np.clip(y + dy, 0, height - 1).astype(np.int32)
            
            # Применяем искажение
            distorted_array = img_array[new_y, new_x]
            
            # Конвертируем обратно в изображение
            distorted_img = Image.fromarray(distorted_array)
            
            return distorted_img
            
        except Exception as e:
            logger.error(f"Error creating wave effect: {e}")
            return image
    
    def _precalculate_wave_frames(self):
        """Предрассчитывает 4 кадра эффекта 'Волна' для статичных слоев"""
        self._wave_frames_cache.clear()
        now = time.time()
        self._wave_last_update = now
        
        for unique_name, original_image in self._image_cache.items():
            if original_image is None:
                continue
                
            # Пропускаем GIF-изображения, они будут обрабатываться в реальном времени
            if unique_name in self._gif_frames:
                continue
                
            # Создаем 4 варианта искажения для статичных изображений
            for frame_idx in range(4):
                cache_key = (unique_name, frame_idx)
                distorted_img = self._create_wave_effect(original_image.copy(), frame_idx)
                if distorted_img:
                    self._wave_frames_cache[cache_key] = distorted_img
        
        logger.info(f"Precalculated {len(self._wave_frames_cache)} wave frames for static images")
    
    def _render_frame_cpu(self):
        """Рендеринг кадра на CPU"""
        visible_layers = self._get_visible_layers()
        frame_image = Image.new("RGBA", (self.width, self.height), (0, 0, 0, 0))
        
        # Обновляем таймер смены кадров эффекта "Волна" только если эффект включен
        if self.wave_enabled:
            now = time.time()
            self._wave_last_update = now
            
            if now - self._wave_frame_timer > self._wave_frame_interval:
                self._current_wave_frame = (self._current_wave_frame + 1) % 4
                self._wave_frame_timer = now
        
        for unique_name in visible_layers:
            # Получаем оригинальное изображение (для GIF - текущий кадр)
            original_image = self._get_layer_image(unique_name)
            if not original_image:
                continue
            
            layer_data = self.layers_by_name.get(unique_name)
            if not layer_data:
                continue
            
            # Применяем эффект волны если он включен
            if self.wave_enabled:
                # Проверяем, является ли изображение GIF
                is_gif = unique_name in self._gif_frames
                
                if is_gif:
                    # Для GIF применяем эффект волны к текущему кадру в реальном времени
                    image = self._create_wave_effect(original_image.copy(), self._current_wave_frame)
                else:
                    # Для статичных изображений используем кэш
                    cache_key = (unique_name, self._current_wave_frame)
                    if cache_key in self._wave_frames_cache:
                        image = self._wave_frames_cache[cache_key]
                    else:
                        # Если нет в кэше, создаем новый
                        image = self._create_wave_effect(original_image.copy(), self._current_wave_frame)
                        self._wave_frames_cache[cache_key] = image
            else:
                # Когда волна выключена, используем оригинальное изображение
                image = original_image
            
            if not image:
                continue
            
            # Применяем прозрачность слоя
            alpha = layer_data.get('alpha', 1.0)
            if alpha < 1.0:
                if image.mode == 'RGBA':
                    # Создаем копию изображения с измененной прозрачностью
                    image = image.copy()
                    r, g, b, a = image.split()
                    a = a.point(lambda x: int(x * alpha))
                    image = Image.merge('RGBA', (r, g, b, a))
            
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
                image = image.resize(new_size, Image.Resampling.BILINEAR)
            
            px = (self.width - image.width) // 2 + int(layer_data.get("x", 0)) + offset_x
            py = (self.height - image.height) // 2 + int(layer_data.get("y", 0)) + offset_y
            
            try:
                frame_image.alpha_composite(image, (px, py))
            except Exception as e:
                logger.error(f"Ошибка композиции слоя {unique_name}: {e}")
        
        return frame_image
    
    def _loop(self):
        """Основной цикл рендеринга"""
        frame_count = 0
        last_fps_time = time.time()
        fps = 0
        
        while self._running:
            if not self.model or not self.model_dir:
                time.sleep(0.1)
                continue
                
            frame_start = time.time()
            
            # Кэширование кадров для статичных сцен
            now = time.time()
            if (self._render_cache and 
                now - self._render_cache_time < self._render_cache_duration and
                abs(self.audio_level - self._render_cache.get('audio_level', 0)) < 0.01):
                frame_image = self._render_cache['image']
            else:
                # Рендерим новый кадр
                frame_image = self._render_frame_cpu()
                
                # Кэшируем
                self._render_cache = {
                    'image': frame_image.copy(),
                    'audio_level': self.audio_level,
                    'time': now
                }
                self._render_cache_time = now
            
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
            
            # Считаем FPS
            frame_count += 1
            if now - last_fps_time >= 1.0:
                fps = frame_count
                frame_count = 0
                last_fps_time = now
                if fps < self.fps * 0.8:
                    logger.warning(f"Low FPS: {fps}/{self.fps}")
            
            # Контроль FPS
            elapsed = time.time() - frame_start
            sleep_time = self._frame_interval - elapsed
            
            if sleep_time > 0:
                time.sleep(sleep_time)
            elif elapsed > self._frame_interval * 1.5:
                logger.warning(f"Frame render took {elapsed*1000:.2f}ms, target: {self._frame_interval*1000:.2f}ms")
    
    def set_wave(self, enabled, amplitude=3.0, frequency=0.5, speed=1.0):
        """Включает/выключает эффект 'Волна' и устанавливает параметры"""
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
            
            # Сбрасываем кэш рендера, чтобы следующий кадр был перерисован без эффектов
            self._render_cache = None
            self._visible_layers_cache_time = 0  # Сбрасываем кэш видимых слоев
        
        logger.info(f"Wave effect: enabled={enabled}, amplitude={amplitude}, frequency={frequency}, speed={speed}")