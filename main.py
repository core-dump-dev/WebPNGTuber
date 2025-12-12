import threading, time
import tkinter as tk
from tkinter import ttk, messagebox
from editor import ModelEditor
from renderer import Renderer
from webserver import WebServer
from audio import AudioProcessor
import os
import json
from PIL import Image, ImageTk
import sounddevice as sd
import sys
import logging
import logging.handlers
from datetime import datetime

# Определение базовой директории
if getattr(sys, 'frozen', False):
    BASE_DIR = os.path.dirname(sys.executable)
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# Создание папки для логов
LOGS_DIR = os.path.join(BASE_DIR, "logs")
os.makedirs(LOGS_DIR, exist_ok=True)

# Настройка логирования для main
def setup_main_logging():
    logger = logging.getLogger('main')
    logger.setLevel(logging.INFO)  # Уменьшили уровень логирования
    
    formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    
    log_file = os.path.join(LOGS_DIR, 'main.log')
    file_handler = logging.handlers.RotatingFileHandler(
        log_file, maxBytes=1048576, backupCount=5
    )
    file_handler.setFormatter(formatter)
    
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    
    logger.addHandler(file_handler)
    logger.addHandler(console_handler)
    
    return logger

logger = setup_main_logging()

MODELS_DIR = os.path.join(BASE_DIR, "models")
os.makedirs(MODELS_DIR, exist_ok=True)
SETTINGS_FILE = os.path.join(BASE_DIR, "settings.json")

class App:
    def __init__(self, root):
        self.root = root
        root.title("WebPNGTuber TG: @memory_not_found")
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        
        logger.info("Application started")
        
        # Загрузка настроек
        self.settings = self.load_settings()
        
        # Инициализация компонентов с оптимизациями
        self.renderer = Renderer(width=700, height=700, fps=60)
        self.audio = AudioProcessor(callback=self.on_audio_level,
                                   device=self.settings.get('mic_device'))
        self.audio.noise_gate_threshold = self.settings.get('noise_gate_threshold', 0.01)
        self.webserver = None
        
        # Настройки
        self.thresholds = self.settings.get('thresholds', {
            'silent': 0.05,
            'whisper': 0.25,
            'normal': 0.6,
            'shout': 0.8
        })
        
        # Эффекты
        self.effects = self.settings.get('effects', {
            'shake': False,
            'bounce': False,
            'pulse': False,
            'blink': True,
            'random_effect': False
        })
        self.renderer.set_effects(self.effects)
        
        # UI
        frame = ttk.Frame(root, padding=8)
        frame.pack(fill="both", expand=True)
        
        # Слоты моделей
        slots_frame = ttk.LabelFrame(frame, text="Слоты моделей (2×3)")
        slots_frame.grid(row=0, column=0, sticky="nsew", padx=4, pady=4)
        
        self.model_slots = []
        self.slot_previews = [None] * 6
        self.current_slot = self.settings.get('current_slot')
        
        try:
            root.iconbitmap(os.path.join(BASE_DIR, 'favicon.ico'))
        except Exception:
            pass
        
        # Оптимизация: предзагрузка превью с кэшированием
        self._slot_preview_cache = {}
        
        for r in range(3):
            for c in range(2):
                idx = r*2 + c
                photo = self._get_slot_preview(idx + 1)
                
                btn = ttk.Button(slots_frame, text=f"Слот {idx+1}\n(пустой)", width=20,
                                 image=photo, compound="top",
                                 command=lambda i=idx: self.load_slot(i))
                btn.grid(row=r, column=c, padx=6, pady=6)
                self.model_slots.append(btn)
        
        # Управление
        ctrl_frame = ttk.LabelFrame(frame, text="Управление")
        ctrl_frame.grid(row=0, column=1, sticky="nsew", padx=4, pady=4)
        
        self.editor_btn = ttk.Button(ctrl_frame, text="Открыть редактор моделей", 
                                     command=self.open_editor)
        self.editor_btn.pack(fill="x", padx=8, pady=6)
        
        self.server_btn = ttk.Button(ctrl_frame, text="Запустить веб-сервер", 
                                     command=self.toggle_server)
        self.server_btn.pack(fill="x", padx=8, pady=6)
        
        # Кнопка ссылки
        self.link_btn = ttk.Button(
            ctrl_frame,
            text="🌐 http://localhost:6969/",
            command=self.open_web_link,
            state="disabled"
        )
        self.link_btn.pack(fill="x", padx=8, pady=2)
        
        # Настройки микрофона
        mic_frame = ttk.LabelFrame(ctrl_frame, text="Микрофон")
        mic_frame.pack(fill="x", padx=8, pady=6)
        
        ttk.Label(mic_frame, text="Устройство ввода:").pack(anchor='w')
        self.device_var = tk.StringVar(value=self.settings.get('mic_device', 'По умолчанию'))
        self.device_combo = ttk.Combobox(mic_frame, textvariable=self.device_var)
        self.device_combo.pack(fill='x')
        
        # Оптимизация: асинхронная загрузка устройств
        self.devices = ["По умолчанию"]  # Начальное значение
        self.device_combo['values'] = self.devices
        self.device_combo.bind('<<ComboboxSelected>>', self.on_device_change)
        
        # Загружаем устройства в фоновом режиме
        threading.Thread(target=self._load_audio_devices, daemon=True).start()
        
        ttk.Label(mic_frame, text="Уровень ввода:").pack(anchor="w")
        self.vol_label = ttk.Label(mic_frame, text="Уровень: 0.00")
        self.vol_label.pack(anchor="w")
        
        # Чувствительность
        sens_frame = ttk.Frame(mic_frame)
        sens_frame.pack(fill="x", pady=2)
        
        ttk.Label(sens_frame, text="Чувствительность").pack(anchor="w")
        self.sensitivity = tk.DoubleVar(value=self._round_to_step(self.settings.get('sensitivity', 1.0), 0.05))
        self.sens_percent_label = ttk.Label(sens_frame, text=f"{self.sensitivity.get()*100:.0f}%")
        self.sens_percent_label.pack(anchor="e")
        
        sens_scale = ttk.Scale(mic_frame, from_=0.1, to=5.0, variable=self.sensitivity, orient="horizontal")
        sens_scale.pack(fill="x")
        sens_scale.configure(command=self._on_sensitivity_scale_move)
        sens_scale.bind("<ButtonRelease-1>", lambda e: self.on_sensitivity_change())
        
        # Подавление шума
        noise_gate_frame = ttk.Frame(mic_frame)
        noise_gate_frame.pack(fill="x", pady=2)
        
        self.noise_gate_enabled = tk.BooleanVar(value=self.settings.get('noise_gate_enabled', True))
        ttk.Checkbutton(noise_gate_frame, text="Подавление шума", variable=self.noise_gate_enabled,
                       command=self.toggle_noise_gate).pack(side="left")
        
        self.noise_gate_value_label = ttk.Label(noise_gate_frame, text="0.010")
        self.noise_gate_value_label.pack(side="right", padx=5)
        
        self.noise_gate_threshold = tk.DoubleVar(value=self._round_to_step(
            self.settings.get('noise_gate_threshold', 0.01), 0.005))
        
        noise_gate_scale = ttk.Scale(mic_frame, from_=0.001, to=0.05, variable=self.noise_gate_threshold,
                                   orient="horizontal")
        noise_gate_scale.pack(fill="x", pady=2)
        noise_gate_scale.configure(command=self._on_noise_gate_scale_move)
        noise_gate_scale.bind("<ButtonRelease-1>", lambda e: self.update_noise_gate_threshold())
        
        # Индикатор уровня
        ttk.Label(mic_frame, text="Индикатор уровня:").pack(anchor="w", pady=(5,0))
        self.level_canvas = tk.Canvas(mic_frame, width=200, height=40, bg="#f0f0f0")
        self.level_canvas.pack(fill="x", pady=5)
        
        # Пороговые линии
        self.threshold_lines = {
            'silent': self.level_canvas.create_line(0, 0, 0, 40, dash=(2,2), width=1),
            'whisper': self.level_canvas.create_line(0, 0, 0, 40, dash=(2,2), width=1),
            'normal': self.level_canvas.create_line(0, 0, 0, 40, dash=(2,2), width=1),
            'shout': self.level_canvas.create_line(0, 0, 0, 40, dash=(2,2), width=1)
        }
        
        # Индикатор
        self.level_indicator = self.level_canvas.create_rectangle(0, 0, 0, 40, outline="", fill="#4CAF50", tags="level_bar")
        self.level_canvas.bind("<Configure>", self.on_canvas_resize)
        
        # Пороги голоса
        thresh_frame = ttk.LabelFrame(ctrl_frame, text="Пороги голоса")
        thresh_frame.pack(fill="x", padx=8, pady=6)
        
        # Оптимизация: группировка виджетов
        self._create_threshold_widgets(thresh_frame)
        
        # Активные состояния
        states_frame = ttk.LabelFrame(ctrl_frame, text="Активные состояния")
        states_frame.pack(fill="x", padx=8, pady=6)
        
        self.state_vars = {
            'silent': tk.BooleanVar(value=self.settings.get('active_states', {}).get('silent', True)),
            'whisper': tk.BooleanVar(value=self.settings.get('active_states', {}).get('whisper', True)),
            'normal': tk.BooleanVar(value=self.settings.get('active_states', {}).get('normal', True)),
            'shout': tk.BooleanVar(value=self.settings.get('active_states', {}).get('shout', True))
        }
        
        self._create_state_widgets(states_frame)
        
        # Эффекты
        effects_frame = ttk.LabelFrame(ctrl_frame, text="Глобальные эффекты")
        effects_frame.pack(fill="x", padx=8, pady=6)
        
        self._create_effect_widgets(effects_frame)
        
        # Idle режим
        idle_frame = ttk.LabelFrame(ctrl_frame, text="Idle-режим")
        idle_frame.pack(fill="x", padx=8, pady=6)
        
        self.idle_enabled = tk.BooleanVar(value=self.settings.get('idle_enabled', False))
        ttk.Checkbutton(idle_frame, text="Включить затемнение в idle", variable=self.idle_enabled,
                       command=self.update_idle_setting).pack(anchor="w", padx=5, pady=2)
        
        ttk.Label(idle_frame, text="Время до затемнения (сек):").pack(anchor="w", padx=5)
        self.idle_timeout = tk.DoubleVar(value=self.settings.get('idle_timeout', 60.0))
        idle_entry = ttk.Entry(idle_frame, textvariable=self.idle_timeout, width=8)
        idle_entry.pack(anchor="w", padx=5, pady=2)
        idle_entry.bind("<Return>", lambda e: self.update_idle_setting())
        
        # Сохранение настроек
        ttk.Button(ctrl_frame, text="Сохранить настройки", command=self.save_settings).pack(fill="x", padx=8, pady=10)
        
        # Запуск
        self.audio.start()
        self.toggle_noise_gate()
        
        self.renderer.start()
        self.renderer.set_thresholds(self.thresholds)
        self.renderer.set_noise_gate(self.noise_gate_threshold.get() if self.noise_gate_enabled.get() else 0.0)
        self.renderer.set_idle(self.idle_enabled.get(), self.idle_timeout.get())
        
        self.update_active_states()
        self.update_threshold_visuals()
        
        self.refresh_slot_buttons()
        
        # Загрузка текущего слота
        if self.current_slot:
            self.load_slot(self.current_slot - 1, silent=True)
    
    def _get_slot_preview(self, slot_num: int) -> Optional[ImageTk.PhotoImage]:
        """Получает превью слота с кэшированием"""
        cache_key = f"slot{slot_num}"
        
        if cache_key in self._slot_preview_cache:
            return self._slot_preview_cache[cache_key]
        
        preview_path = os.path.join(MODELS_DIR, f"slot{slot_num}", "preview.png")
        
        if os.path.exists(preview_path):
            try:
                img = Image.open(preview_path)
                photo = ImageTk.PhotoImage(img)
                self._slot_preview_cache[cache_key] = photo
                return photo
            except Exception:
                pass
        
        return None
    
    def _load_audio_devices(self):
        """Асинхронная загрузка аудиоустройств"""
        try:
            devices = sd.query_devices()
            input_devices = ["По умолчанию"]
            
            for i, dev in enumerate(devices):
                if dev.get('max_input_channels', 0) > 0:
                    name = dev.get('name', '')
                    if "CABLE" in name or "VB-Audio" in name or "Voicemee" in name or "virtual" in name.lower():
                        continue
                    input_devices.append(name)
            
            # Обновляем в основном потоке
            self.root.after(0, lambda: self._update_device_list(input_devices))
            
        except Exception as e:
            logger.error(f"Error getting audio devices: {e}")
    
    def _update_device_list(self, devices: list):
        """Обновляет список устройств в UI"""
        self.devices = devices
        self.device_combo['values'] = devices
    
    def _create_threshold_widgets(self, parent):
        """Создает виджеты для порогов"""
        ttk.Label(parent, text="Тишина:").grid(row=0, column=0, sticky="w", padx=2)
        self.silent_thresh = tk.DoubleVar(value=self.thresholds['silent'])
        silent_entry = ttk.Entry(parent, textvariable=self.silent_thresh, width=8)
        silent_entry.grid(row=0, column=1, padx=2)
        silent_entry.bind("<Return>", lambda e: self.update_thresholds())
        
        ttk.Label(parent, text="Шёпот:").grid(row=0, column=2, sticky="w", padx=2)
        self.whisper_thresh = tk.DoubleVar(value=self.thresholds['whisper'])
        whisper_entry = ttk.Entry(parent, textvariable=self.whisper_thresh, width=8)
        whisper_entry.grid(row=0, column=3, padx=2)
        whisper_entry.bind("<Return>", lambda e: self.update_thresholds())
        
        ttk.Label(parent, text="Норма:").grid(row=1, column=0, sticky="w", padx=2)
        self.normal_thresh = tk.DoubleVar(value=self.thresholds['normal'])
        normal_entry = ttk.Entry(parent, textvariable=self.normal_thresh, width=8)
        normal_entry.grid(row=1, column=1, padx=2)
        normal_entry.bind("<Return>", lambda e: self.update_thresholds())
        
        ttk.Label(parent, text="Крик:").grid(row=1, column=2, sticky="w", padx=2)
        self.shout_thresh = tk.DoubleVar(value=self.thresholds['shout'])
        shout_entry = ttk.Entry(parent, textvariable=self.shout_thresh, width=8)
        shout_entry.grid(row=1, column=3, padx=2)
        shout_entry.bind("<Return>", lambda e: self.update_thresholds())
        
        ttk.Button(parent, text="Применить", command=self.update_thresholds).grid(
            row=2, column=0, columnspan=4, pady=4, sticky="ew")
        
        help_label = ttk.Label(
            parent,
            text="Значения: 0.0-1.0 (0=мин, 1=макс громкость)",
            font=("Arial", 8)
        )
        help_label.grid(row=3, column=0, columnspan=4, pady=(0,4))
    
    def _create_state_widgets(self, parent):
        """Создает виджеты для состояний"""
        ttk.Checkbutton(parent, text="Тишина", variable=self.state_vars['silent'],
                       command=self.update_active_states).grid(row=0, column=0, sticky="w", padx=5)
        ttk.Checkbutton(parent, text="Шёпот", variable=self.state_vars['whisper'],
                       command=self.update_active_states).grid(row=0, column=1, sticky="w", padx=5)
        ttk.Checkbutton(parent, text="Норма", variable=self.state_vars['normal'],
                       command=self.update_active_states).grid(row=1, column=0, sticky="w", padx=5)
        ttk.Checkbutton(parent, text="Крик", variable=self.state_vars['shout'],
                       command=self.update_active_states).grid(row=1, column=1, sticky="w", padx=5)
    
    def _create_effect_widgets(self, parent):
        """Создает виджеты для эффектов"""
        self.shake = tk.BooleanVar(value=self.effects.get('shake', False))
        ttk.Checkbutton(parent, text="Дрожание", variable=self.shake,
                       command=self.update_effects).pack(anchor="w")
        
        self.bounce = tk.BooleanVar(value=self.effects.get('bounce', False))
        ttk.Checkbutton(parent, text="Прыжки", variable=self.bounce,
                       command=self.update_effects).pack(anchor="w")
        
        self.pulse = tk.BooleanVar(value=self.effects.get('pulse', False))
        ttk.Checkbutton(parent, text="Пульсация", variable=self.pulse,
                       command=self.update_effects).pack(anchor="w")
        
        self.blink = tk.BooleanVar(value=self.effects.get('blink', True))
        ttk.Checkbutton(parent, text="Моргание (глаза)", variable=self.blink,
                       command=self.update_effects).pack(anchor="w")
        
        self.random_effect = tk.BooleanVar(value=self.effects.get('random_effect', False))
        ttk.Checkbutton(parent, text="Случайная смена состояний", variable=self.random_effect,
                       command=self.update_effects).pack(anchor="w")
    
    def _round_to_step(self, value, step):
        return round(value / step) * step
    
    def _on_sensitivity_scale_move(self, value):
        rounded_value = self._round_to_step(float(value), 0.05)
        self.sensitivity.set(rounded_value)
        self.sens_percent_label.config(text=f"{rounded_value*100:.0f}%")
    
    def _on_noise_gate_scale_move(self, value):
        rounded_value = self._round_to_step(float(value), 0.005)
        self.noise_gate_threshold.set(rounded_value)
        self.noise_gate_value_label.config(text=f"{rounded_value:.3f}")
    
    def load_settings(self):
        if os.path.exists(SETTINGS_FILE):
            try:
                with open(SETTINGS_FILE, 'r') as f:
                    return json.load(f)
            except Exception as e:
                logger.error(f"Error loading settings: {e}")
        return {}
    
    def save_settings(self):
        settings = {
            'thresholds': self.thresholds,
            'active_states': {state: var.get() for state, var in self.state_vars.items()},
            'effects': {
                'shake': self.shake.get(),
                'bounce': self.bounce.get(),
                'pulse': self.pulse.get(),
                'blink': self.blink.get(),
                'random_effect': self.random_effect.get()
            },
            'sensitivity': self.sensitivity.get(),
            'noise_gate_enabled': self.noise_gate_enabled.get(),
            'noise_gate_threshold': self.noise_gate_threshold.get(),
            'mic_device': self.device_var.get(),
            'idle_enabled': self.idle_enabled.get(),
            'idle_timeout': self.idle_timeout.get(),
            'current_slot': self.current_slot
        }
        
        try:
            with open(SETTINGS_FILE, 'w') as f:
                json.dump(settings, f, indent=2)
            messagebox.showinfo("Настройки сохранены", "Настройки успешно сохранены.")
            logger.info("Settings saved successfully")
        except Exception as e:
            messagebox.showerror("Ошибка сохранения", f"Не удалось сохранить настройки: {e}")
            logger.error(f"Error saving settings: {e}")
    
    def refresh_slot_buttons(self):
        """Обновляет кнопки слотов"""
        if not hasattr(self, "model_slots"):
            return
        
        for idx in range(6):
            slot_dir = os.path.join(MODELS_DIR, f"slot{idx+1}")
            json_path = os.path.join(slot_dir, "model.json")
            
            btn = self.model_slots[idx]
            
            is_current = (idx + 1 == self.current_slot)
            prefix = "★ " if is_current else ""
            
            if os.path.exists(json_path):
                try:
                    with open(json_path, "r", encoding="utf-8") as f:
                        model_data = json.load(f)
                    model_name = model_data.get('name', f"Слот {idx+1}")
                    btn.config(text=f"{prefix}Слот {idx+1}\n{model_name}")
                except Exception as e:
                    btn.config(text=f"{prefix}Слот {idx+1}\n(ошибка)")
                    logger.error(f"Error loading model from slot {idx+1}: {e}")
            else:
                btn.config(text=f"{prefix}Слот {idx+1}\n(пустой)")
            
            # Обновляем превью
            photo = self._get_slot_preview(idx + 1)
            if photo:
                btn.config(image=photo)
            else:
                btn.config(image='')
    
    def update_active_states(self):
        active_states = {}
        for state, var in self.state_vars.items():
            active_states[state] = var.get()
        self.renderer.set_active_states(active_states)
        self.update_threshold_visuals()
        logger.info(f"Active states updated: {active_states}")
    
    def update_thresholds(self):
        self.thresholds = {
            'silent': self.silent_thresh.get(),
            'whisper': self.whisper_thresh.get(),
            'normal': self.normal_thresh.get(),
            'shout': self.shout_thresh.get()
        }
        self.renderer.set_thresholds(self.thresholds)
        self.update_threshold_visuals()
        logger.info(f"Thresholds updated: {self.thresholds}")
    
    def update_threshold_visuals(self):
        canvas_width = self.level_canvas.winfo_width()
        if canvas_width < 10:
            return
        
        self.level_canvas.delete("threshold_label")
        
        for key in self.thresholds:
            if not self.state_vars.get(key, tk.BooleanVar(value=True)).get():
                self.level_canvas.coords(self.threshold_lines[key], -10, 0, -10, 40)
                continue
                
            try:
                val = float(self.thresholds[key])
            except Exception:
                val = 0.0
            
            pos = min(1.0, max(0.0, val)) * canvas_width
            self.level_canvas.coords(self.threshold_lines[key], pos, 0, pos, 40)
            
            anchor = "center"
            if key == "silent":
                anchor = "e"
            elif key == "shout":
                anchor = "w"
            
            self.level_canvas.create_text(
                pos, 10,
                text=key,
                anchor=anchor,
                tags="threshold_label",
                font=("Arial", 8)
            )
    
    def update_level_indicator(self, level):
        canvas_width = self.level_canvas.winfo_width()
        if canvas_width < 10:
            return
        
        level_clamped = min(1.0, max(0.0, float(level)))
        indicator_width = level_clamped * canvas_width
        
        try:
            self.level_canvas.coords(self.level_indicator, 0, 0, indicator_width, 40)
        except Exception as e:
            logger.error(f"Error updating level indicator: {e}")
        
        try:
            t = self.thresholds
            s = float(t.get('silent', 0.05))
            w = float(t.get('whisper', 0.25))
            n = float(t.get('normal', 0.6))
            
            if level_clamped <= s:
                color = "#888888"
            elif level_clamped <= w:
                color = "#2196F3"
            elif level_clamped <= n:
                color = "#4CAF50"
            else:
                color = "#f44336"
            
            self.level_canvas.itemconfig(self.level_indicator, fill=color)
        except Exception as e:
            logger.error(f"Error setting level indicator color: {e}")
    
    def on_canvas_resize(self, event=None):
        self.update_threshold_visuals()
        self.update_level_indicator(self.audio_level_scaled if hasattr(self, 'audio_level_scaled') else 0)
    
    def load_slot(self, idx, silent=False):
        slot_dir = os.path.join(MODELS_DIR, f"slot{idx+1}")
        json_path = os.path.join(slot_dir, "model.json")
        
        if not os.path.exists(json_path):
            if not silent:
                answer = messagebox.askyesno("Нет модели",
                    f"В слоте {idx+1} нет модели. Создать новую?")
                if not answer:
                    return
            
            self.renderer.model = {"name": f"Слот {idx+1}", "layers": [], "groups": []}
            self.renderer.model_dir = slot_dir
            os.makedirs(slot_dir, exist_ok=True)
            
            with open(json_path, "w", encoding="utf-8") as f:
                json.dump(self.renderer.model, f, indent=2, ensure_ascii=False)
        else:
            with open(json_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            
            # Фильтруем данные перед загрузкой
            filtered_data = {}
            filtered_data["name"] = data.get("name", f"Слот {idx+1}")
            filtered_data["width"] = data.get("width", 700)
            filtered_data["height"] = data.get("height", 700)
            
            # Фильтруем слои
            filtered_layers = []
            for layer in data.get("layers", []):
                filtered_layer = {}
                valid_fields = ["name", "file", "x", "y", "scale", "rotation", 
                            "flip_horizontal", "flip_vertical", "visible", 
                            "is_gif", "group"]
                
                for field in valid_fields:
                    if field in layer:
                        filtered_layer[field] = layer[field]
                
                filtered_layers.append(filtered_layer)
            
            filtered_data["layers"] = filtered_layers
            
            # Фильтруем группы
            filtered_groups = []
            for group in data.get("groups", []):
                filtered_group = {}
                valid_fields = ["name", "children", "parent", "logic", 
                            "blink_freq", "random_effect", "random_min", "random_max"]
                
                for field in valid_fields:
                    if field in group:
                        filtered_group[field] = group[field]
                
                # Убедимся, что logic - это словарь
                if "logic" not in filtered_group:
                    filtered_group["logic"] = {}
                
                filtered_groups.append(filtered_group)
            
            filtered_data["groups"] = filtered_groups
            
            self.renderer.load_model(filtered_data, slot_dir)
            
            if self.webserver:
                self.webserver.renderer = self.renderer
        
        self.current_slot = idx + 1
        self.refresh_slot_buttons()
        
        model_name = self.renderer.model.get('name','модель')
        preview_path = os.path.join(slot_dir, "preview.png")
        
        if os.path.exists(preview_path):
            try:
                img = Image.open(preview_path)
                photo = ImageTk.PhotoImage(img)
                self.slot_previews[idx] = photo
                self.model_slots[idx].config(image=photo)
            except Exception as e:
                logger.error(f"Error loading preview for slot {idx+1}: {e}")
        
        if not silent:
            logger.info(f"Model loaded from slot {idx+1}: {model_name}")
            messagebox.showinfo("Загружено", f"Модель загружена из слота {idx+1}")
    
    def open_editor(self):
        try:
            main_window = self.root
            main_window.attributes('-disabled', True)
            
            editor = ModelEditor(
                main_window,
                on_save=self.on_model_saved,
                device=self.device_var.get(),
                noise_gate_threshold=self.noise_gate_threshold.get() if self.noise_gate_enabled.get() else 0.0,
                sensitivity=self.sensitivity.get(),
                thresholds=self.thresholds,
                current_slot=self.current_slot
            )
            
            def on_editor_close():
                try:
                    editor.audio_processor.stop()
                except Exception as e:
                    logger.error(f"Error stopping editor audio: {e}")
                
                main_window.attributes('-disabled', False)
                main_window.focus_set()
                self.refresh_slot_buttons()
                
                try:
                    self.audio.stop()
                    self.audio = AudioProcessor(
                        callback=self.on_audio_level,
                        device=self.device_var.get()
                    )
                    self.audio.noise_gate_threshold = self.noise_gate_threshold.get() if self.noise_gate_enabled.get() else 0.0
                    self.audio.set_sensitivity(self.sensitivity.get())
                    self.audio.start()
                except Exception as e:
                    logger.error(f"Error restarting audio: {e}")
                
                editor.destroy()
            
            editor.protocol("WM_DELETE_WINDOW", on_editor_close)
            
        except Exception as e:
            import traceback
            tb = traceback.format_exc()
            with open("error.log", "w", encoding="utf-8") as f:
                f.write(tb)
            messagebox.showerror("Ошибка редактора", f"Не удалось открыть редактор: {e}. Смотри error.log")
            logger.error(f"Error opening editor: {e}\n{tb}")
            self.root.attributes('-disabled', False)
    
    def on_model_saved(self, model_data, model_dir, slot_num=None):
        self.refresh_slot_buttons()
        logger.info(f"Model saved to directory: {model_dir}")
        
        if slot_num == self.current_slot:
            logger.info(f"Model saved to current slot {slot_num}, updating display only")
            self.refresh_slot_buttons()
    
    def toggle_server(self):
        if self.webserver and getattr(self.webserver, "is_running", False):
            self.webserver.stop()
            self.server_btn.config(text="Запустить веб-сервер")
            self.link_btn.config(state="disabled")
            logger.info("Web server stopped")
        else:
            if not self.webserver:
                self.webserver = WebServer(self.renderer)
            elif not self.webserver.is_running:
                self.webserver.renderer = self.renderer
            
            try:
                self.webserver.start()
                self.server_btn.config(text="Остановить веб-сервер")
                self.link_btn.config(state="normal")
                logger.info("Web server started")
            except Exception as e:
                logger.error(f"Error starting web server: {e}")
                messagebox.showerror("Ошибка", f"Не удалось запустить веб-сервер: {e}")
    
    def on_audio_level(self, level):
        try:
            self.audio_level_scaled = level * self.sensitivity.get()
        except Exception as e:
            self.audio_level_scaled = level
            logger.error(f"Error scaling audio level: {e}")
        
        try:
            self.vol_label.config(text=f"Уровень: {self.audio_level_scaled:.2f}")
        except Exception as e:
            logger.error(f"Error updating volume label: {e}")
        
        self.update_level_indicator(self.audio_level_scaled)
        self.renderer.set_audio_level(self.audio_level_scaled)
    
    def on_device_change(self, event):
        device_name = self.device_var.get()
        try:
            self.audio.stop()
        except Exception as e:
            logger.error(f"Error stopping audio: {e}")
        
        self.audio = AudioProcessor(callback=self.on_audio_level, device=device_name)
        self.audio.noise_gate_threshold = self.noise_gate_threshold.get() if self.noise_gate_enabled.get() else 0.0
        self.audio.start()
        logger.info(f"Audio device changed to: {device_name}")
    
    def on_sensitivity_change(self):
        self.audio.set_sensitivity(self.sensitivity.get())
        logger.info(f"Sensitivity changed to: {self.sensitivity.get()}")
    
    def toggle_noise_gate(self):
        enabled = self.noise_gate_enabled.get()
        threshold = self.noise_gate_threshold.get() if enabled else 0.0
        self.audio.noise_gate_threshold = threshold
        self.renderer.set_noise_gate(threshold)
        logger.info(f"Noise gate {'enabled' if enabled else 'disabled'} with threshold: {threshold}")
    
    def update_noise_gate_threshold(self):
        if self.noise_gate_enabled.get():
            threshold = self.noise_gate_threshold.get()
            self.audio.noise_gate_threshold = threshold
            self.renderer.set_noise_gate(threshold)
            logger.info(f"Noise gate threshold updated to: {threshold}")
    
    def update_effects(self):
        effects = {
            'shake': self.shake.get(),
            'bounce': self.bounce.get(),
            'pulse': self.pulse.get(),
            'blink': self.blink.get(),
            'random_effect': self.random_effect.get()
        }
        self.renderer.set_effects(effects)
        logger.info(f"Effects updated: {effects}")
    
    def update_idle_setting(self):
        enabled = self.idle_enabled.get()
        timeout = self.idle_timeout.get()
        self.renderer.set_idle(enabled, timeout)
        logger.info(f"Idle mode updated: enabled={enabled}, timeout={timeout}")
    
    def open_web_link(self):
        import webbrowser
        try:
            url = "http://localhost:6969/"
            webbrowser.open(url)
            logger.info(f"Opened web link: {url}")
        except Exception as e:
            logger.error(f"Error opening web link: {e}")
            messagebox.showerror("Ошибка", f"Не удалось открыть ссылку: {e}")
    
    def on_close(self):
        try:
            self.audio.stop()
        except Exception as e:
            logger.error(f"Error stopping audio: {e}")
        
        try:
            self.renderer.stop()
        except Exception as e:
            logger.error(f"Error stopping renderer: {e}")
        
        if self.webserver:
            try:
                self.webserver.stop()
            except Exception as e:
                logger.error(f"Error stopping web server: {e}")
        
        self.link_btn.config(state="disabled")
        self.save_settings()
        logger.info("Application closed")
        self.root.destroy()

if __name__ == "__main__":
    root = tk.Tk()
    app = App(root)
    root.mainloop()