import threading, time
from PIL import Image, ImageOps, ImageEnhance
import os, io, math, random

class Renderer:
    def __init__(self, width=512, height=512, fps=30):
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
        
        # Active voice states
        self.active_states = {
            'silent': True,
            'whisper': True,
            'normal': True,
            'shout': True
        }
        
        # Ordered states by volume level
        self.state_order = ['silent', 'whisper', 'normal', 'shout']

    def set_active_states(self, active_states):
        """Set active voice states"""
        self.active_states = active_states
        
        # Update state order based on active states
        self.state_order = [state for state in self.state_order if active_states.get(state, True)]

    def set_thresholds(self, thresholds):
        """Set voice level thresholds"""
        self.thresholds = thresholds

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
            if os.path.exists(fp):
                try:
                    self._image_cache[layer.get("name")] = Image.open(fp).convert("RGBA")
                except:
                    pass
        for g in self.model.get("groups", []):
            name = g.get("name")
            if name not in self.group_blink_timers:
                self.group_blink_timers[name] = time.time() + random.uniform(2.0,6.0)
                self.group_blink_until[name] = 0.0

    def set_audio_level(self, level):
        """Set current audio level"""
        self.audio_level = max(0.0, float(level))

    def get_frame_bytes(self):
        """Get current frame as PNG bytes"""
        with self._lock:
            return self._frame_bytes

    def _choose_group_child(self, group):
        """Choose appropriate child for group based on audio level and active states"""
        # Handle zero audio level immediately
        if self.audio_level <= 0.001:
            return self._get_active_state(group, 'silent')
        
        # Find the highest active state that matches the current audio level
        for state in reversed(self.state_order):
            if self.audio_level >= self.thresholds.get(state, 0) and self.active_states.get(state, True):
                return self._get_active_state(group, state)
        
        # Fallback to silent if no active state matches
        return self._get_active_state(group, 'silent')

    def _get_active_state(self, group, state):
        """Get active state with fallbacks"""
        logic = group.get("logic", {})
        chosen_name = logic.get(state)
        
        # If the desired state is not set, find the closest active state
        if not chosen_name:
            # Find the closest active state below the requested state
            state_idx = self.state_order.index(state) if state in self.state_order else -1
            if state_idx > 0:
                for i in range(state_idx - 1, -1, -1):
                    lower_state = self.state_order[i]
                    if self.active_states.get(lower_state, True):
                        chosen_name = logic.get(lower_state)
                        if chosen_name:
                            break
            
            # If still not found, find the closest active state above
            if not chosen_name and state_idx < len(self.state_order) - 1:
                for i in range(state_idx + 1, len(self.state_order)):
                    higher_state = self.state_order[i]
                    if self.active_states.get(higher_state, True):
                        chosen_name = logic.get(higher_state)
                        if chosen_name:
                            break
        
        # Handle blinking
        now = time.time()
        group_name = group.get("name")
        blink_freq = float(group.get("blink_freq", 0.0))
        
        if group_name not in self.group_blink_timers:
            self.group_blink_timers[group_name] = now + random.uniform(2.0, 6.0)
            self.group_blink_until[group_name] = 0.0
            
        if blink_freq > 0.001:
            if now > self.group_blink_timers.get(group_name, 0):
                self.group_blink_until[group_name] = now + 0.12
                self.group_blink_timers[group_name] = now + random.uniform(blink_freq * 0.7, blink_freq * 1.3)
            
            if now < self.group_blink_until.get(group_name, 0):
                if "blink" in logic:
                    chosen_name = logic.get("blink")
                else:
                    for child in group.get("children", []):
                        if any(kw in child.lower() for kw in ["close", "closed", "shut"]):
                            chosen_name = child
                            break
        
        return chosen_name

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
                    
                    # Get image
                    image = None
                    if name in self._image_cache:
                        image = self._image_cache[name]
                    else:
                        img_path = os.path.join(self.model_dir, layer.get("file") or "")
                        if os.path.exists(img_path):
                            try:
                                image = Image.open(img_path).convert("RGBA")
                                self._image_cache[name] = image
                            except:
                                pass
                    
                    if not image:
                        continue
                    
                    # Apply speech effect if needed
                    if layer.get("speech"):
                        scale = 1.0 + max(0, self.audio_level-0.05) * 0.8
                        w, h = image.size
                        try:
                            image = image.resize((int(w), int(h*scale)), resample=Image.BILINEAR)
                        except:
                            pass
                    
                    # Position and composite
                    px = (self.width - image.size[0])//2 + int(layer.get("x", 0))
                    py = (self.height - image.size[1])//2 + int(layer.get("y", 0))
                    try:
                        img.alpha_composite(image, (px, py))
                    except Exception:
                        pass
            
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