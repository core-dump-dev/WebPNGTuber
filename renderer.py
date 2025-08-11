import threading, time
from PIL import Image, ImageOps, ImageEnhance, ImageSequence
import os, io, math, random

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
        
        # Active voice states
        self.active_states = {
            'silent': True,
            'whisper': True,
            'normal': True,
            'shout': True
        }
        
        # Ordered states by volume level
        self.state_order = ['silent', 'whisper', 'normal', 'shout']
        self.effects = {}  # Глобальные эффекты
        
        # Для случайного эффекта
        self.group_random_timers = {}
        self.group_random_current = {}

    def set_noise_gate(self, threshold):
        """Set noise gate threshold"""
        self.noise_gate = threshold

    def set_effects(self, effects):
        """Set global effects"""
        self.effects = effects

    def set_thresholds(self, thresholds):
        """Set voice level thresholds"""
        self.thresholds = thresholds

    def set_active_states(self, active_states):
        """Set which voice states are active"""
        self.active_states = active_states

    def start(self):
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self):
        self._running = False
        if self._thread:
            self._thread.join(timeout=1.0)

    def load_model(self, model_json, model_dir):
        """Load PNGTuber model"""
        self.model = model_json
        self.model_dir = model_dir
        self._image_cache = {}
        for layer in self.model.get("layers", []):
            fp = os.path.join(self.model_dir, layer.get("file") or "")
            if not os.path.exists(fp):
                continue
            try:
                # Обработка GIF-анимаций
                if layer.get("is_gif", False):
                    img = Image.open(fp)
                    frames = []
                    for frame in ImageSequence.Iterator(img):
                        frames.append(frame.copy().convert("RGBA"))
                    self._image_cache[layer.get("name")] = frames
                else:
                    self._image_cache[layer.get("name")] = Image.open(fp).convert("RGBA")
            except:
                pass
        
        # Инициализация эффектов
        for g in self.model.get("groups", []):
            name = g.get("name")
            if name not in self.group_blink_timers:
                self.group_blink_timers[name] = time.time() + random.uniform(2.0,6.0)
                self.group_blink_until[name] = 0.0
            
            # Инициализация случайного эффекта
            if g.get("random_effect", False):
                self.group_random_timers[name] = time.time()
                self.group_random_current[name] = None

    def set_audio_level(self, level):
        """Set current audio level with noise gate"""
        if level < self.noise_gate:
            level = 0.0
        self.audio_level = max(0.0, float(level))

    def get_frame_bytes(self):
        """Get current frame as PNG bytes"""
        with self._lock:
            return self._frame_bytes

    def _choose_group_child(self, group):
        """Choose appropriate child for group based on audio level and active states"""
        group_name = group.get("name")
        
        # Обработка случайного эффекта
        if group.get("random_effect", False) and self.effects.get('random_effect', False):
            now = time.time()
            min_time = group.get("random_min", 5.0)
            max_time = group.get("random_max", 10.0)
            
            # Если пришло время сменить состояние
            if now > self.group_random_timers.get(group_name, 0):
                children = group.get("children", [])
                if children:
                    # Исключаем состояния blink и open из случайного выбора
                    blink_layer = group.get("logic", {}).get("blink", "")
                    open_layer = group.get("logic", {}).get("open", "")
                    available = [c for c in children if c != blink_layer and c != open_layer]
                    
                    if available:
                        chosen = random.choice(available)
                        self.group_random_current[group_name] = chosen
                
                # Устанавливаем следующее время смены
                interval = random.uniform(min_time, max_time)
                self.group_random_timers[group_name] = now + interval
            
            # Возвращаем текущее случайное состояние
            if self.group_random_current.get(group_name):
                return self.group_random_current.get(group_name)
        
        # Обработка моргания
        if self.effects.get('blink', True):
            now = time.time()
            group_name = group.get("name")
            blink_freq = float(group.get("blink_freq", 0.0))
            
            if group_name not in self.group_blink_timers:
                self.group_blink_timers[group_name] = now + random.uniform(2.0, 6.0)
                self.group_blink_until[group_name] = 0.0
                
            if blink_freq > 0.001:
                if now > self.group_blink_timers.get(group_name, 0):
                    self.group_blink_until[group_name] = now + 0.12
                    self.group_blink_timers[group_name] = now + blink_freq
                
                if now < self.group_blink_until.get(group_name, 0):
                    if "blink" in group.get("logic", {}):
                        return group.get("logic", {}).get("blink")
                    else:
                        for child in group.get("children", []):
                            if any(kw in child.lower() for kw in ["close", "closed", "shut"]):
                                return child
        
        # Обработка голосовых состояний
        current_state = "silent"
        if self.audio_level > self.thresholds['shout']:
            current_state = "shout"
        elif self.audio_level > self.thresholds['normal']:
            current_state = "normal"
        elif self.audio_level > self.thresholds['whisper']:
            current_state = "whisper"
        elif self.audio_level > self.thresholds['silent']:
            current_state = "silent"
        
        logic = group.get("logic", {})
        # Если определено состояние "open", используем его как основное
        open_layer = logic.get("open")
        if open_layer and current_state != "blink":
            return open_layer
        
        # Иначе используем стандартную логику
        for state in reversed(self.state_order):
            if self.audio_level >= self.thresholds.get(state, 0) and self.active_states.get(state, True):
                return logic.get(state) or logic.get("normal") or logic.get("whisper") or logic.get("silent")
        
        # Fallback to silent if no active state matches
        return logic.get("silent")

    def _loop(self):
        """Main rendering loop"""
        frame_time = 1.0 / self.fps
        while self._running:
            start = time.time()
            img = Image.new("RGBA", (self.width, self.height), (0,0,0,0))
            if self.model and self.model_dir:
                # Build group choices
                group_choices = {}
                for group in self.model.get("groups", []):
                    chosen = self._choose_group_child(group)
                    if chosen:
                        group_choices[group['name']] = chosen
                
                # Render layers
                layers_by_name = {l.get("name"): l for l in self.model.get("layers", [])}
                for layer in self.model.get("layers", []):
                    name = layer.get("name")
                    group_name = layer.get("group")
                    
                    # Skip if group has chosen another child
                    if group_name and group_name in group_choices:
                        if name != group_choices[group_name]:
                            continue
                    
                    if not layer.get("visible", True):
                        continue
                    
                    # Get image - handle GIF animation
                    image = None
                    if name in self._image_cache:
                        cached = self._image_cache[name]
                        
                        # Если это анимация
                        if isinstance(cached, list) and cached:
                            # Вычисляем текущий кадр
                            frame_index = int(time.time() * 10) % len(cached)
                            image = cached[frame_index]
                        else:
                            image = cached
                    
                    if not image:
                        continue
                    
                    # Apply global effects
                    orig_image = image.copy()  # Сохраняем оригинал
                    
                    if self.effects.get('shake', False):
                        # Shake effect
                        shake_intensity = min(1.0, self.audio_level * 5)
                        offset_x = int((random.random() - 0.5) * 10 * shake_intensity)
                        offset_y = int((random.random() - 0.5) * 10 * shake_intensity)
                    else:
                        offset_x, offset_y = 0, 0
                        
                    if self.effects.get('pulse', False):
                        # Pulse effect
                        pulse_scale = 1.0 + (math.sin(time.time() * 5) * 0.1 * self.audio_level)
                        new_size = (int(image.width * pulse_scale), int(image.height * pulse_scale))
                        image = image.resize(new_size, Image.LANCZOS)
                    
                    # Position and composite
                    px = (self.width - image.size[0])//2 + int(layer.get("x", 0)) + offset_x
                    py = (self.height - image.size[1])//2 + int(layer.get("y", 0)) + offset_y
                    try:
                        img.alpha_composite(image, (px, py))
                    except Exception:
                        pass
                    
                    # Восстанавливаем оригинал для следующей итерации
                    image = orig_image
            
            # Convert to PNG bytes
            with io.BytesIO() as buf:
                img.save(buf, format="PNG")
                data = buf.getvalue()
            with self._lock:
                self._frame_bytes = data
            
            # Maintain FPS
            elapsed = time.time() - start
            to_sleep = frame_time - elapsed
            if to_sleep > 0:
                time.sleep(to_sleep)