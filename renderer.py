import threading, time
from PIL import Image, ImageEnhance, ImageSequence
import os, io, math, random
import logging
import logging.handlers
from datetime import datetime

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
        
        # Активные состояния голоса
        self.active_states = {
            'silent': True,
            'whisper': True,
            'normal': True,
            'shout': True
        }
        
        # Порядок состояний по громкости
        self.state_order = ['silent', 'whisper', 'normal', 'shout']
        self.effects = {}
        
        # Для случайного эффекта
        self.group_random_timers = {}
        self.group_random_current = {}
        
        # Для GIF анимации
        self._gif_frames = {}
        self._gif_frame_times = {}
        self._gif_last_update = {}
        self._gif_current_frame = {}

        # Idle режим
        self.idle_enabled = False
        self.idle_timeout = 60.0
        self.last_activity_time = time.time()
        self.idle_brightness = 0.5

        # Словари для быстрого доступа
        self.layers_by_name = {}
        self.groups_by_name = {}
        
        logger.info("Renderer initialized")

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
        self._image_cache = {}
        self._gif_frames = {}
        self._gif_frame_times = {}
        self._gif_last_update = {}
        self._gif_current_frame = {}
        
        self.layers_by_name = {l.get('name'): l for l in self.model.get('layers', [])}
        self.groups_by_name = {g.get('name'): g for g in self.model.get('groups', [])}
        
        for layer in self.model.get("layers", []):
            filename = layer.get("file")
            if not filename:
                continue
                
            fp = os.path.join(self.model_dir, filename)
            if not os.path.exists(fp):
                continue
                
            try:
                scale = float(layer.get("scale", 1.0))
                rotation = int(layer.get("rotation", 0))
                flip_h = bool(layer.get("flip_horizontal", False))
                flip_v = bool(layer.get("flip_vertical", False))

                if layer.get("is_gif", False):
                    self._gif_frames[layer.get("name")] = []
                    self._gif_frame_times[layer.get("name")] = []
                    self._gif_current_frame[layer.get("name")] = 0
                    self._gif_last_update[layer.get("name")] = 0
                    img = Image.open(fp)
                    
                    for frame in range(img.n_frames):
                        img.seek(frame)
                        frame_img = img.copy().convert("RGBA")
                        
                        # Применяем трансформации в правильном порядке
                        # 1. Масштаб
                        if scale != 1.0:
                            new_width = max(1, int(frame_img.width * scale))
                            new_height = max(1, int(frame_img.height * scale))
                            frame_img = frame_img.resize((new_width, new_height), Image.LANCZOS)
                            
                        # 2. Поворот
                        if rotation != 0:
                            frame_img = frame_img.rotate(rotation, expand=True, resample=Image.BICUBIC)
                            
                        # 3. Зеркалирование (применяется после всех остальных трансформаций)
                        if flip_h:
                            frame_img = frame_img.transpose(Image.FLIP_LEFT_RIGHT)
                        if flip_v:
                            frame_img = frame_img.transpose(Image.FLIP_TOP_BOTTOM)
                            
                        self._gif_frames[layer.get("name")].append(frame_img)
                        try:
                            duration = img.info.get('duration', 100) / 1000.0
                            self._gif_frame_times[layer.get("name")].append(duration)
                        except:
                            self._gif_frame_times[layer.get("name")].append(0.1)
                else:
                    image = Image.open(fp).convert("RGBA")
                    
                    # Применяем трансформации в правильном порядке
                    # 1. Масштаб
                    if scale != 1.0:
                        new_width = max(1, int(image.width * scale))
                        new_height = max(1, int(image.height * scale))
                        image = image.resize((new_width, new_height), Image.LANCZOS)
                        
                    # 2. Поворот
                    if rotation != 0:
                        image = image.rotate(rotation, expand=True, resample=Image.BICUBIC)
                        
                    # 3. Зеркалирование (применяется после всех остальных трансформаций)
                    if flip_h:
                        image = image.transpose(Image.FLIP_LEFT_RIGHT)
                    if flip_v:
                        image = image.transpose(Image.FLIP_TOP_BOTTOM)
                        
                    self._image_cache[layer.get("name")] = image
            except Exception as e:
                logger.error(f"Ошибка загрузки изображения: {e}")
        
        for g in self.model.get("groups", []):
            name = g.get("name")
            if name not in self.group_blink_timers:
                self.group_blink_timers[name] = time.time() + random.uniform(2.0,6.0)
                self.group_blink_until[name] = 0.0
            
            if g.get("random_effect", False):
                self.group_random_timers[name] = time.time()
                self.group_random_current[name] = None
        
        logger.info(f"Model loaded: {model_json.get('name', 'unnamed')}")

    def set_audio_level(self, level):
        if level < self.noise_gate:
            level = 0.0
        self.audio_level = max(0.0, float(level))
        
        # Обновляем время активности только если звук выше минимального активного порога
        if self.idle_enabled and level > self._get_min_active_threshold():
            self.last_activity_time = time.time()

    def _get_min_active_threshold(self):
        """Получает минимальный порог из активных состояний"""
        min_threshold = 1.0  # Максимальное значение по умолчанию
        
        for state, active in self.active_states.items():
            if active and state in self.thresholds:
                threshold = self.thresholds[state]
                if threshold < min_threshold:
                    min_threshold = threshold
        
        return min_threshold if min_threshold < 1.0 else 0.0

    def get_frame_bytes(self):
        with self._lock:
            return self._frame_bytes

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
        
        # Обработка моргания
        if self.effects.get('blink', True):
            now = time.time()
            blink_freq = float(group.get("blink_freq", 0.0))
            
            if group_name not in self.group_blink_timers:
                self.group_blink_timers[group_name] = now + random.uniform(2.0, 6.0)
                self.group_blink_until[group_name] = 0.0
                
            if blink_freq > 0.001:
                if now > self.group_blink_timers.get(group_name, 0):
                    self.group_blink_until[group_name] = now + 0.12
                    self.group_blink_timers[group_name] = now + blink_freq
                
                if now < self.group_blink_until.get(group_name, 0):
                    if "blink" in logic:
                        return logic["blink"]
                    else:
                        for child in group.get("children", []):
                            if any(kw in child.lower() for kw in ["close", "closed", "shut", "blink"]):
                                return child
        
        # Использование состояния "open"
        open_layer = logic.get("open")
        if open_layer:
            return open_layer
        
        # Обработка случайного эффекта
        if group.get("random_effect", False) and self.effects.get('random_effect', False):
            now = time.time()
            min_time = group.get("random_min", 5.0)
            max_time = group.get("random_max", 10.0)
            
            if now > self.group_random_timers.get(group_name, 0):
                children = group.get("children", [])
                if children:
                    blink_layer = logic.get("blink", "")
                    open_layer = logic.get("open", "")
                    available = [c for c in children if c != blink_layer and c != open_layer]
                    
                    if available:
                        chosen = random.choice(available)
                        self.group_random_current[group_name] = chosen
                
                interval = random.uniform(min_time, max_time)
                self.group_random_timers[group_name] = now + interval
            
            if self.group_random_current.get(group_name):
                return self.group_random_current.get(group_name)
        
        # Обработка голосовых состояний
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
            return logic.get(current_state)
        
        for state in reversed(self.state_order):
            if state == current_state:
                continue
            if self.audio_level >= self.thresholds.get(state, 0) and self.active_states.get(state, True):
                if state in logic:
                    return logic.get(state)
        
        return logic.get("silent")

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

    def _loop(self):
        frame_time = 1.0 / self.fps
        while self._running:
            start = time.time()
            img = Image.new("RGBA", (self.width, self.height), (0,0,0,0))
            if self.model and self.model_dir:
                group_choices = {}
                for group in self.model.get("groups", []):
                    chosen = self._choose_group_child(group)
                    if chosen:
                        resolved = self._resolve_to_layer(chosen, group['name'])
                        if resolved:
                            group_choices[group['name']] = resolved
                
                layers_by_name = {l.get("name"): l for l in self.model.get("layers", [])}
                for layer in self.model.get("layers", []):
                    name = layer.get("name")
                    group_name = layer.get("group")
                    
                    if group_name and group_name in group_choices:
                        if name != group_choices[group_name]:
                            continue
                    
                    if not layer.get("visible", True):
                        continue
                    
                    image = self._get_layer_image(name)
                    if not image:
                        continue
                    
                    orig_image = image.copy()
                    
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
                    
                    px = (self.width - image.width) // 2 + int(layer.get("x", 0)) + offset_x
                    py = (self.height - image.height) // 2 + int(layer.get("y", 0)) + offset_y
                    try:
                        img.alpha_composite(image, (px, py))
                    except Exception as e:
                        logger.error(f"Ошибка композиции слоя {name}: {e}")
                    
                    image = orig_image
            
            # Применение idle-режима
            if self.idle_enabled:
                current_time = time.time()
                if current_time - self.last_activity_time > self.idle_timeout:
                    enhancer = ImageEnhance.Brightness(img)
                    img = enhancer.enhance(self.idle_brightness)

            with io.BytesIO() as buf:
                img.save(buf, format="PNG")
                data = buf.getvalue()
            with self._lock:
                self._frame_bytes = data
            
            elapsed = time.time() - start
            to_sleep = frame_time - elapsed
            if to_sleep > 0:
                time.sleep(to_sleep)