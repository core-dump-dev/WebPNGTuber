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
    logger.setLevel(logging.DEBUG)
    
    # Форматирование
    formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    
    # Файловый обработчик с ротацией
    log_file = os.path.join(LOGS_DIR, 'main.log')
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

        # Инициализация компонентов
        self.renderer = Renderer(width=700, height=700, fps=60)
        self.audio = AudioProcessor(callback=self.on_audio_level,
                                   device=self.settings.get('mic_device'))
        self.audio.noise_gate_threshold = self.settings.get('noise_gate_threshold', 0.01)
        self.webserver = None

        # Настройки по умолчанию
        self.thresholds = self.settings.get('thresholds', {
            'silent': 0.05,
            'whisper': 0.25,
            'normal': 0.6,
            'shout': 0.8
        })

        # Глобальные эффекты
        self.effects = self.settings.get('effects', {
            'shake': False,
            'bounce': False,
            'pulse': False,
            'blink': True,
            'random_effect': False
        })
        self.renderer.set_effects(self.effects)

        # UI layout
        frame = ttk.Frame(root, padding=8)
        frame.pack(fill="both", expand=True)

        # Слоты моделей
        slots_frame = ttk.LabelFrame(frame, text="Слоты моделей (2×3)")
        slots_frame.grid(row=0, column=0, sticky="nsew", padx=4, pady=4)
        self.model_slots = []
        self.slot_previews = [None] * 6

        try:
            root.iconbitmap(os.path.join(BASE_DIR, 'favicon.ico'))
        except Exception:
            pass

        for r in range(3):
            for c in range(2):
                idx = r*2 + c
                preview_path = os.path.join(MODELS_DIR, f"slot{idx+1}", "preview.png")
                photo = None
                if os.path.exists(preview_path):
                    try:
                        img = Image.open(preview_path)
                        photo = ImageTk.PhotoImage(img)
                        self.slot_previews[idx] = photo
                    except:
                        photo = None

                btn = ttk.Button(slots_frame, text=f"Слот {idx+1}\n(пустой)", width=20,
                                 image=photo, compound="top",
                                 command=lambda i=idx: self.load_slot(i))
                btn.grid(row=r, column=c, padx=6, pady=6)
                self.model_slots.append(btn)

        # Управление
        ctrl_frame = ttk.LabelFrame(frame, text="Управление")
        ctrl_frame.grid(row=0, column=1, sticky="nsew", padx=4, pady=4)

        self.editor_btn = ttk.Button(ctrl_frame, text="Открыть редактор моделей", command=self.open_editor)
        self.editor_btn.pack(fill="x", padx=8, pady=6)

        self.server_btn = ttk.Button(ctrl_frame, text="Запустить веб-сервер", command=self.toggle_server)
        self.server_btn.pack(fill="x", padx=8, pady=6)

        # Настройки микрофона
        mic_frame = ttk.LabelFrame(ctrl_frame, text="Микрофон")
        mic_frame.pack(fill="x", padx=8, pady=6)

        # Выбор устройства
        ttk.Label(mic_frame, text="Устройство ввода:").pack(anchor='w')
        self.device_var = tk.StringVar(value=self.settings.get('mic_device', 'По умолчанию'))
        self.device_combo = ttk.Combobox(mic_frame, textvariable=self.device_var)
        self.device_combo.pack(fill='x')

        # Заполнение устройств
        self.devices = self.get_audio_devices()
        self.device_combo['values'] = self.devices
        self.device_combo.bind('<<ComboboxSelected>>', self.on_device_change)

        ttk.Label(mic_frame, text="Уровень ввода:").pack(anchor="w")
        self.vol_label = ttk.Label(mic_frame, text="Уровень: 0.00")
        self.vol_label.pack(anchor="w")

        # Чувствительность с шагом 5%
        sens_frame = ttk.Frame(mic_frame)
        sens_frame.pack(fill="x", pady=2)
        
        ttk.Label(sens_frame, text="Чувствительность").pack(anchor="w")
        self.sensitivity = tk.DoubleVar(value=self._round_to_step(self.settings.get('sensitivity', 1.0), 0.05))
        self.sens_percent_label = ttk.Label(sens_frame, text=f"{self.sensitivity.get()*100:.0f}%")
        self.sens_percent_label.pack(anchor="e")
        
        # Шкала чувствительности с шагом 5% (0.05)
        sens_scale = ttk.Scale(mic_frame, from_=0.1, to=5.0, variable=self.sensitivity, orient="horizontal")
        sens_scale.pack(fill="x")
        sens_scale.configure(command=self._on_sensitivity_scale_move)
        sens_scale.bind("<ButtonRelease-1>", lambda e: self.on_sensitivity_change())

        # Подавление шума с настройкой мощности и шагом 0.005
        noise_gate_frame = ttk.Frame(mic_frame)
        noise_gate_frame.pack(fill="x", pady=2)
        
        self.noise_gate_enabled = tk.BooleanVar(value=self.settings.get('noise_gate_enabled', True))
        ttk.Checkbutton(noise_gate_frame, text="Подавление шума", variable=self.noise_gate_enabled,
                       command=self.toggle_noise_gate).pack(side="left")
        
        # Текущее значение подавления шума
        self.noise_gate_value_label = ttk.Label(noise_gate_frame, text="0.010")
        self.noise_gate_value_label.pack(side="right", padx=5)
        
        self.noise_gate_threshold = tk.DoubleVar(value=self._round_to_step(self.settings.get('noise_gate_threshold', 0.01), 0.005))
        
        # Шкала подавления шума с шагом 0.005
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

        # Индикатор уровня
        self.level_indicator = self.level_canvas.create_rectangle(0, 0, 0, 40, outline="", fill="#4CAF50", tags="level_bar")

        # Обработка изменения размера
        self.level_canvas.bind("<Configure>", self.on_canvas_resize)

        # Пороги голоса
        thresh_frame = ttk.LabelFrame(ctrl_frame, text="Пороги голоса")
        thresh_frame.pack(fill="x", padx=8, pady=6)

        ttk.Label(thresh_frame, text="Тишина:").grid(row=0, column=0, sticky="w", padx=2)
        self.silent_thresh = tk.DoubleVar(value=self.thresholds['silent'])
        silent_entry = ttk.Entry(thresh_frame, textvariable=self.silent_thresh, width=8)
        silent_entry.grid(row=0, column=1, padx=2)
        silent_entry.bind("<Return>", lambda e: self.update_thresholds())

        ttk.Label(thresh_frame, text="Шёпот:").grid(row=0, column=2, sticky="w", padx=2)
        self.whisper_thresh = tk.DoubleVar(value=self.thresholds['whisper'])
        whisper_entry = ttk.Entry(thresh_frame, textvariable=self.whisper_thresh, width=8)
        whisper_entry.grid(row=0, column=3, padx=2)
        whisper_entry.bind("<Return>", lambda e: self.update_thresholds())

        ttk.Label(thresh_frame, text="Норма:").grid(row=1, column=0, sticky="w", padx=2)
        self.normal_thresh = tk.DoubleVar(value=self.thresholds['normal'])
        normal_entry = ttk.Entry(thresh_frame, textvariable=self.normal_thresh, width=8)
        normal_entry.grid(row=1, column=1, padx=2)
        normal_entry.bind("<Return>", lambda e: self.update_thresholds())

        ttk.Label(thresh_frame, text="Крик:").grid(row=1, column=2, sticky="w", padx=2)
        self.shout_thresh = tk.DoubleVar(value=self.thresholds['shout'])
        shout_entry = ttk.Entry(thresh_frame, textvariable=self.shout_thresh, width=8)
        shout_entry.grid(row=1, column=3, padx=2)
        shout_entry.bind("<Return>", lambda e: self.update_thresholds())

        ttk.Button(thresh_frame, text="Применить", command=self.update_thresholds).grid(
            row=2, column=0, columnspan=4, pady=4, sticky="ew")

        help_label = ttk.Label(
            thresh_frame,
            text="Значения: 0.0-1.0 (0=мин, 1=макс громкость)",
            font=("Arial", 8)
        )
        help_label.grid(row=3, column=0, columnspan=4, pady=(0,4))

        # Активные состояния голоса
        states_frame = ttk.LabelFrame(ctrl_frame, text="Активные состояния")
        states_frame.pack(fill="x", padx=8, pady=6)

        self.state_vars = {
            'silent': tk.BooleanVar(value=self.settings.get('active_states', {}).get('silent', True)),
            'whisper': tk.BooleanVar(value=self.settings.get('active_states', {}).get('whisper', True)),
            'normal': tk.BooleanVar(value=self.settings.get('active_states', {}).get('normal', True)),
            'shout': tk.BooleanVar(value=self.settings.get('active_states', {}).get('shout', True))
        }

        ttk.Checkbutton(states_frame, text="Тишина", variable=self.state_vars['silent'],
                       command=self.update_active_states).grid(row=0, column=0, sticky="w", padx=5)
        ttk.Checkbutton(states_frame, text="Шёпот", variable=self.state_vars['whisper'],
                       command=self.update_active_states).grid(row=0, column=1, sticky="w", padx=5)
        ttk.Checkbutton(states_frame, text="Норма", variable=self.state_vars['normal'],
                       command=self.update_active_states).grid(row=1, column=0, sticky="w", padx=5)
        ttk.Checkbutton(states_frame, text="Крик", variable=self.state_vars['shout'],
                       command=self.update_active_states).grid(row=1, column=1, sticky="w", padx=5)

        # Глобальные эффекты
        effects_frame = ttk.LabelFrame(ctrl_frame, text="Глобальные эффекты")
        effects_frame.pack(fill="x", padx=8, pady=6)
        
        self.shake = tk.BooleanVar(value=self.effects.get('shake', False))
        ttk.Checkbutton(effects_frame, text="Дрожание", variable=self.shake,
                       command=self.update_effects).pack(anchor="w")
        
        self.bounce = tk.BooleanVar(value=self.effects.get('bounce', False))
        ttk.Checkbutton(effects_frame, text="Прыжки", variable=self.bounce,
                       command=self.update_effects).pack(anchor="w")
        
        self.pulse = tk.BooleanVar(value=self.effects.get('pulse', False))
        ttk.Checkbutton(effects_frame, text="Пульсация", variable=self.pulse,
                       command=self.update_effects).pack(anchor="w")
        
        self.blink = tk.BooleanVar(value=self.effects.get('blink', True))
        ttk.Checkbutton(effects_frame, text="Моргание (глаза)", variable=self.blink,
                       command=self.update_effects).pack(anchor="w")
        
        self.random_effect = tk.BooleanVar(value=self.effects.get('random_effect', False))
        ttk.Checkbutton(effects_frame, text="Случайная смена состояний", variable=self.random_effect,
                       command=self.update_effects).pack(anchor="w")

        # Настройки idle-режима
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

        # Запуск обработки аудио
        self.audio.start()
        self.toggle_noise_gate()

        # Запуск рендерера
        self.renderer.start()
        self.renderer.set_thresholds(self.thresholds)
        self.renderer.set_noise_gate(self.noise_gate_threshold.get() if self.noise_gate_enabled.get() else 0.0)
        self.renderer.set_idle(self.idle_enabled.get(), self.idle_timeout.get())

        # Применение начальных состояний
        self.update_active_states()

        # Обновление визуализации порогов
        self.update_threshold_visuals()

        # Обновление слотов
        self.refresh_slot_buttons()

    def _round_to_step(self, value, step):
        """Округление значения до ближайшего шага"""
        return round(value / step) * step

    def _on_sensitivity_scale_move(self, value):
        """Обработка движения шкалы чувствительности"""
        # Округляем до шага 5%
        rounded_value = self._round_to_step(float(value), 0.05)
        self.sensitivity.set(rounded_value)
        self.sens_percent_label.config(text=f"{rounded_value*100:.0f}%")

    def _on_noise_gate_scale_move(self, value):
        """Обработка движения шкалы подавления шума"""
        # Округляем до шага 0.005
        rounded_value = self._round_to_step(float(value), 0.005)
        self.noise_gate_threshold.set(rounded_value)
        self.noise_gate_value_label.config(text=f"{rounded_value:.3f}")

    def get_audio_devices(self):
        """Получение списка аудиоустройств"""
        try:
            devices = sd.query_devices()
            input_devices = ["По умолчанию"]
            
            for i, dev in enumerate(devices):
                if dev.get('max_input_channels', 0) > 0:
                    name = dev.get('name', '')
                    if "CABLE" in name or "VB-Audio" in name or "Voicemee" in name or "virtual" in name.lower():
                        continue
                    input_devices.append(name)
            
            return input_devices
        except Exception as e:
            logger.error(f"Error getting audio devices: {e}")
            return ["По умолчанию"]

    def on_device_change(self, event):
        """Смена аудиоустройства"""
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
        """Изменение чувствительности"""
        self.audio.set_sensitivity(self.sensitivity.get())
        logger.info(f"Sensitivity changed to: {self.sensitivity.get()}")

    def toggle_noise_gate(self):
        """Переключение подавления шума"""
        enabled = self.noise_gate_enabled.get()
        threshold = self.noise_gate_threshold.get() if enabled else 0.0
        self.audio.noise_gate_threshold = threshold
        self.renderer.set_noise_gate(threshold)
        logger.info(f"Noise gate {'enabled' if enabled else 'disabled'} with threshold: {threshold}")

    def update_noise_gate_threshold(self):
        """Обновление порога подавления шума"""
        if self.noise_gate_enabled.get():
            threshold = self.noise_gate_threshold.get()
            self.audio.noise_gate_threshold = threshold
            self.renderer.set_noise_gate(threshold)
            logger.info(f"Noise gate threshold updated to: {threshold}")

    def update_effects(self):
        """Обновление эффектов"""
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
        """Обновление настройки idle-режима"""
        enabled = self.idle_enabled.get()
        timeout = self.idle_timeout.get()
        self.renderer.set_idle(enabled, timeout)
        logger.info(f"Idle mode updated: enabled={enabled}, timeout={timeout}")

    def load_settings(self):
        """Загрузка настроек"""
        if os.path.exists(SETTINGS_FILE):
            try:
                with open(SETTINGS_FILE, 'r') as f:
                    return json.load(f)
            except Exception as e:
                logger.error(f"Error loading settings: {e}")
        return {}
    
    def save_settings(self):
        """Сохранение настроек"""
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
            'idle_timeout': self.idle_timeout.get()
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
        """Обновление кнопок слотов"""
        if not hasattr(self, "model_slots"):
            return

        for idx in range(6):
            slot_dir = os.path.join(MODELS_DIR, f"slot{idx+1}")
            json_path = os.path.join(slot_dir, "model.json")

            btn = self.model_slots[idx]
            preview_path = os.path.join(slot_dir, "preview.png")

            if os.path.exists(json_path):
                try:
                    with open(json_path, "r", encoding="utf-8") as f:
                        model_data = json.load(f)
                    model_name = model_data.get('name', f"Слот {idx+1}")
                    btn.config(text=f"Слот {idx+1}\n{model_name}")
                except Exception as e:
                    btn.config(text=f"Слот {idx+1}\n(ошибка)")
                    logger.error(f"Error loading model from slot {idx+1}: {e}")
            else:
                btn.config(text=f"Слот {idx+1}\n(пустой)")

            if os.path.exists(preview_path):
                try:
                    img = Image.open(preview_path)
                    photo = ImageTk.PhotoImage(img)
                    self.slot_previews[idx] = photo
                    btn.config(image=photo)
                except Exception as e:
                    btn.config(image='')
                    logger.error(f"Error loading preview for slot {idx+1}: {e}")
            else:
                btn.config(image='')

    def update_active_states(self):
        """Обновление активных состояний"""
        active_states = {}
        for state, var in self.state_vars.items():
            active_states[state] = var.get()
        self.renderer.set_active_states(active_states)
        self.update_threshold_visuals()  # Обновляем визуализацию порогов
        logger.info(f"Active states updated: {active_states}")

    def update_thresholds(self):
        """Обновление порогов голоса"""
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
        """Обновление визуализации порогов - только активные состояния"""
        canvas_width = self.level_canvas.winfo_width()
        if canvas_width < 10:
            return

        # Удаляем старые метки
        self.level_canvas.delete("threshold_label")

        for key in self.thresholds:
            # Показываем только активные состояния
            if not self.state_vars.get(key, tk.BooleanVar(value=True)).get():
                # Скрываем линию неактивного состояния
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
        """Обновление индикатора уровня"""
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
        """Обработка изменения размера канваса"""
        self.update_threshold_visuals()
        self.update_level_indicator(self.audio_level_scaled if hasattr(self, 'audio_level_scaled') else 0)

    def load_slot(self, idx):
        """Загрузка модели из слота"""
        slot_dir = os.path.join(MODELS_DIR, f"slot{idx+1}")
        json_path = os.path.join(slot_dir, "model.json")

        if not os.path.exists(json_path):
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
            self.renderer.load_model(data, slot_dir)
            # Обновляем веб-сервер, если он запущен
            if self.webserver and self.webserver.is_running:
                self.webserver.renderer = self.renderer

        model_name = self.renderer.model.get('name','модель')
        self.model_slots[idx].config(text=f"Слот {idx+1}\n{model_name}")

        preview_path = os.path.join(slot_dir, "preview.png")
        if os.path.exists(preview_path):
            try:
                img = Image.open(preview_path)
                photo = ImageTk.PhotoImage(img)
                self.slot_previews[idx] = photo
                self.model_slots[idx].config(image=photo)
            except Exception as e:
                logger.error(f"Error loading preview for slot {idx+1}: {e}")

        logger.info(f"Model loaded from slot {idx+1}: {model_name}")
        messagebox.showinfo("Загружено", f"Модель загружена из слота {idx+1}")

    def open_editor(self):
        """Открытие редактора моделей"""
        try:
            main_window = self.root
            main_window.attributes('-disabled', True)
            
            editor = ModelEditor(
                main_window, 
                on_save=self.on_model_saved,
                device=self.device_var.get(),
                noise_gate_threshold=self.noise_gate_threshold.get() if self.noise_gate_enabled.get() else 0.0,
                sensitivity=self.sensitivity.get(),
                thresholds=self.thresholds
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

    def on_model_saved(self, model_data, model_dir):
        """Обработка сохранения модели"""
        self.renderer.load_model(model_data, model_dir)
        if self.webserver:
            self.webserver.renderer = self.renderer
        logger.info("Model saved and loaded into renderer")

    def toggle_server(self):
        """Переключение веб-сервера"""
        if self.webserver and getattr(self.webserver, "is_running", False):
            self.webserver.stop()
            self.server_btn.config(text="Запустить веб-сервер")
            logger.info("Web server stopped")
        else:
            self.webserver = WebServer(self.renderer)
            self.webserver.start()
            self.server_btn.config(text="Остановить веб-сервер")
            logger.info("Web server started")

    def on_audio_level(self, level):
        """Обработка уровня аудио"""
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

    def on_close(self):
        """Обработка закрытия приложения"""
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
        self.save_settings()
        logger.info("Application closed")
        self.root.destroy()

if __name__ == "__main__":
    root = tk.Tk()
    app = App(root)
    root.mainloop()