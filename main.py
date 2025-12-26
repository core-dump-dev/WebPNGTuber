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
import psutil

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

# Повышение приоритета процесса для лучшей производительности
try:
    p = psutil.Process()
    if sys.platform == 'win32':
        p.nice(psutil.HIGH_PRIORITY_CLASS)
    else:
        p.nice(-10)
    logger.info("Process priority increased")
except Exception as e:
    logger.warning(f"Could not increase process priority: {e}")

class App:
    def __init__(self, root):
        self.root = root
        root.title("WebPNGTuber TG: @memory_not_found")
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        
        # Установка размера окна: компактное
        root.geometry("800x450")
        root.minsize(800, 450)
        
        logger.info("Application started")

        # Флаг инициализации для предотвращения сохранения при начальной загрузке
        self.initializing = True
        
        # Оптимизации Tkinter
        root.update_idletasks()  # Обновление всех отложенных задач
        root.option_add('*tearOff', False)
        
        # Оптимизация частоты обновления UI
        self._ui_update_interval = 50  # 20 FPS для UI
        self._last_ui_update = 0
        
        # Загрузка настроек
        self.settings = self.load_settings()

        # Инициализация компонентов
        self.renderer = Renderer(width=700, height=700, fps=60)
        self.audio = AudioProcessor(callback=self.on_audio_level,
                                   device=self.settings.get('mic_device'))
        self.audio.noise_gate_threshold = self.settings.get('noise_gate_threshold', 0.01)
        self.webserver = None
        self.renderer_was_started = False  # Флаг для отслеживания запуска рендерера

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
            'random_effect': False,
            'wave': False  # Переименован с 'distortion' на 'wave'
        })
        
        # Параметры эффекта "Волна"
        self.wave_params = self.settings.get('wave_params', {
            'amplitude': 3.0,
            'frequency': 0.5,
            'speed': 1.0
        })
        
        self.renderer.set_effects(self.effects)
        # Устанавливаем параметры эффекта "Волна"
        self.renderer.set_wave(
            self.effects.get('wave', False),
            self.wave_params.get('amplitude', 3.0),
            self.wave_params.get('frequency', 0.5),
            self.wave_params.get('speed', 1.0)
        )

        # Состояние раскрытия секций (по умолчанию все открыты)
        self.sections_state = self.settings.get('sections_state', {
            'thresh': True,
            'states': True,
            'effects': True,
            'idle': True
        })

        # UI layout - три колонки в ряд
        main_frame = ttk.Frame(root, padding=3)
        main_frame.pack(fill="both", expand=True)
        
        # Настройка весов колонок
        main_frame.columnconfigure(0, weight=0)  # Модели - фиксированная ширина
        main_frame.columnconfigure(1, weight=1)  # Основные настройки
        main_frame.columnconfigure(2, weight=2)  # Расширенные настройки

        try:
            root.iconbitmap(os.path.join(BASE_DIR, 'favicon.ico'))
        except Exception:
            pass

        # ---- КОЛОНКА 1: Модели ----
        models_frame = ttk.LabelFrame(main_frame, text="Модели (6 слотов)")
        models_frame.grid(row=0, column=0, sticky="nsew", padx=(0, 3), pady=0)
        
        self.model_slots = []
        self.slot_previews = [None] * 6
        self.current_slot = self.settings.get('current_slot')  # Загружаем текущий слот из настроек

        # Создаем сетку 3x2 для слотов
        slots_grid = ttk.Frame(models_frame)
        slots_grid.pack(fill="both", expand=True, padx=2, pady=2)

        for r in range(3):
            for c in range(2):
                idx = r*2 + c
                
                # Загружаем превью для каждого слота
                photo = self.load_preview_for_slot(idx)
                
                # Создаем фрейм для кнопки с минимумом паддингов
                btn_frame = ttk.Frame(slots_grid)
                btn_frame.grid(row=r, column=c, padx=1, pady=1, sticky="nsew")
                
                btn = ttk.Button(btn_frame, text=f"Слот {idx+1}", 
                                image=photo, compound="top",
                                command=lambda i=idx: self.load_slot(i))
                btn.pack(fill="both", expand=True, padx=0, pady=0)
                
                # Сохраняем ссылку на изображение
                if photo:
                    btn.photo = photo
                    
                self.model_slots.append(btn)
        
        # Равномерное распределение кнопок
        slots_grid.columnconfigure(0, weight=1)
        slots_grid.columnconfigure(1, weight=1)
        for r in range(3):
            slots_grid.rowconfigure(r, weight=1)

        # ---- КОЛОНКА 2: Основные настройки (микрофон и управление) ----
        settings_frame = ttk.LabelFrame(main_frame, text="Основные настройки")
        settings_frame.grid(row=0, column=1, sticky="nsew", padx=3, pady=0)
        
        # Управление
        control_frame = ttk.LabelFrame(settings_frame, text="Управление")
        control_frame.pack(fill="x", pady=(0, 3), padx=3)

        self.editor_btn = ttk.Button(control_frame, text="📝 Открыть редактор", 
                                     command=self.open_editor)
        self.editor_btn.pack(fill="x", padx=2, pady=2)

        self.server_btn = ttk.Button(control_frame, text="🌐 Запустить веб-сервер", 
                                     command=self.toggle_server)
        self.server_btn.pack(fill="x", padx=2, pady=2)

        # Кнопка для открытия ссылки веб-сервера
        self.link_btn = ttk.Button(
            control_frame, 
            text="🔗 Открыть ссылку",
            command=self.open_web_link,
            state="disabled"  # Изначально отключена
        )
        self.link_btn.pack(fill="x", padx=2, pady=2)

        # Настройки микрофона
        mic_frame = ttk.LabelFrame(settings_frame, text="🎤 Микрофон")
        mic_frame.pack(fill="x", pady=(0, 3), padx=3)

        # Выбор устройства
        ttk.Label(mic_frame, text="Устройство:").pack(anchor='w', padx=2, pady=(2, 0))
        self.device_var = tk.StringVar(value=self.settings.get('mic_device', 'По умолчанию'))
        self.device_combo = ttk.Combobox(mic_frame, textvariable=self.device_var, width=18)
        self.device_combo.pack(fill='x', padx=2, pady=(0, 2))

        # Заполнение устройств
        self.devices = self.get_audio_devices()
        self.device_combo['values'] = self.devices
        self.device_combo.bind('<<ComboboxSelected>>', self.on_device_change)

        ttk.Label(mic_frame, text="Уровень:").pack(anchor="w", padx=2, pady=(2, 0))
        self.vol_label = ttk.Label(mic_frame, text="0.00")
        self.vol_label.pack(anchor="w", padx=2, pady=(0, 2))

        # Чувствительность с шагом 5%
        sens_frame = ttk.Frame(mic_frame)
        sens_frame.pack(fill="x", padx=2, pady=0)
        
        ttk.Label(sens_frame, text="Чувствительность:").pack(anchor="w", side="left")
        self.sensitivity = tk.DoubleVar(value=self._round_to_step(self.settings.get('sensitivity', 1.0), 0.05))
        self.sens_percent_label = ttk.Label(sens_frame, text=f"{self.sensitivity.get()*100:.0f}%")
        self.sens_percent_label.pack(anchor="e", side="right")
        
        # Шкала чувствительности с шагом 5% (0.05)
        sens_scale = ttk.Scale(mic_frame, from_=0.1, to=5.0, variable=self.sensitivity, orient="horizontal", length=180)
        sens_scale.pack(fill="x", padx=2, pady=1)
        sens_scale.configure(command=self._on_sensitivity_scale_move)
        sens_scale.bind("<ButtonRelease-1>", lambda e: self.on_sensitivity_change())

        # Подавление шума с настройкой мощности и шагом 0.005
        noise_gate_frame = ttk.Frame(mic_frame)
        noise_gate_frame.pack(fill="x", padx=2, pady=0)
        
        self.noise_gate_enabled = tk.BooleanVar(value=self.settings.get('noise_gate_enabled', True))
        ttk.Checkbutton(noise_gate_frame, text="Подавление шума", variable=self.noise_gate_enabled,
                       command=self.toggle_noise_gate).pack(side="left")
        
        # Текущее значение подавления шума
        self.noise_gate_value_label = ttk.Label(noise_gate_frame, text="0.010")
        self.noise_gate_value_label.pack(side="right", padx=2)
        
        self.noise_gate_threshold = tk.DoubleVar(value=self._round_to_step(self.settings.get('noise_gate_threshold', 0.01), 0.005))
        
        # Шкала подавления шума с шагом 0.005
        noise_gate_scale = ttk.Scale(mic_frame, from_=0.001, to=0.05, variable=self.noise_gate_threshold, 
                                   orient="horizontal", length=180)
        noise_gate_scale.pack(fill="x", padx=2, pady=1)
        noise_gate_scale.configure(command=self._on_noise_gate_scale_move)
        noise_gate_scale.bind("<ButtonRelease-1>", lambda e: self.update_noise_gate_threshold())

        # Индикатор уровня
        ttk.Label(mic_frame, text="Индикатор:").pack(anchor="w", padx=2, pady=(3, 0))
        self.level_canvas = tk.Canvas(mic_frame, width=180, height=25, bg="#f0f0f0")
        self.level_canvas.pack(fill="x", padx=2, pady=(0, 3))

        # Пороговые линии
        self.threshold_lines = {
            'silent': self.level_canvas.create_line(0, 0, 0, 25, dash=(2,2), width=1, fill="#888888"),
            'whisper': self.level_canvas.create_line(0, 0, 0, 25, dash=(2,2), width=1, fill="#2196F3"),
            'normal': self.level_canvas.create_line(0, 0, 0, 25, dash=(2,2), width=1, fill="#4CAF50"),
            'shout': self.level_canvas.create_line(0, 0, 0, 25, dash=(2,2), width=1, fill="#f44336")
        }

        # Индикатор уровня
        self.level_indicator = self.level_canvas.create_rectangle(0, 0, 0, 25, outline="", fill="#4CAF50", tags="level_bar")

        # Обработка изменения размера
        self.level_canvas.bind("<Configure>", self.on_canvas_resize)

        # ---- КОЛОНКА 3: Расширенные настройки ----
        expandable_frame = ttk.LabelFrame(main_frame, text="Расширенные настройки")
        expandable_frame.grid(row=0, column=2, sticky="nsew", padx=(3, 0), pady=0)
        
        # Создаем Canvas с прокруткой для третьей колонки
        self.expand_canvas = tk.Canvas(expandable_frame, bg="#f0f0f0")
        expand_scrollbar = ttk.Scrollbar(expandable_frame, orient="vertical", command=self.expand_canvas.yview)
        self.expand_canvas.configure(yscrollcommand=expand_scrollbar.set)
        
        expand_scrollbar.pack(side="right", fill="y")
        self.expand_canvas.pack(side="left", fill="both", expand=True)
        
        # Фрейм для содержимого внутри canvas
        self.expand_content = ttk.Frame(self.expand_canvas)
        self.expand_canvas.create_window((0, 0), window=self.expand_content, anchor="nw", width=350)  # Уменьшили ширину
        
        # Функция обновления прокрутки
        def configure_scrollregion(event):
            self.expand_canvas.configure(scrollregion=self.expand_canvas.bbox("all"))
        
        self.expand_content.bind("<Configure>", configure_scrollregion)

        # Пороги голоса (сворачиваемые)
        thresh_frame = ttk.Frame(self.expand_content)
        thresh_frame.pack(fill="x", pady=(0, 3))
        thresh_header = ttk.Frame(thresh_frame)
        thresh_header.pack(fill="x")
        
        # Устанавливаем начальное состояние на основе настроек
        self.thresh_expanded = self.sections_state.get('thresh', True)
        thresh_text = "▼ Пороги голоса" if self.thresh_expanded else "▶ Пороги голоса"
        self.thresh_header_label = ttk.Label(thresh_header, text=thresh_text, font=("Arial", 9, "bold"), cursor="hand2")
        self.thresh_header_label.pack(side="left", padx=2, pady=2)
        thresh_header.bind("<Button-1>", lambda e: self.toggle_section("thresh"))
        self.thresh_header_label.bind("<Button-1>", lambda e: self.toggle_section("thresh"))
        self.thresh_content = ttk.Frame(thresh_frame)
        
        # Показываем или скрываем контент в зависимости от состояния
        if self.thresh_expanded:
            self.thresh_content.pack(fill="x", padx=3, pady=(0, 2))

        # Сетка для порогов
        thresholds_grid = ttk.Frame(self.thresh_content)
        thresholds_grid.pack(fill="x", padx=2, pady=2)
        
        ttk.Label(thresholds_grid, text="Тишина:").grid(row=0, column=0, sticky="w", padx=1, pady=1)
        self.silent_thresh = tk.DoubleVar(value=self.thresholds['silent'])
        silent_entry = ttk.Entry(thresholds_grid, textvariable=self.silent_thresh, width=6)
        silent_entry.grid(row=0, column=1, padx=1, pady=1)
        silent_entry.bind("<Return>", lambda e: self.update_thresholds())
        silent_entry.bind("<FocusOut>", lambda e: self.update_thresholds())
        
        ttk.Label(thresholds_grid, text="Шёпот:").grid(row=0, column=2, sticky="w", padx=1, pady=1)
        self.whisper_thresh = tk.DoubleVar(value=self.thresholds['whisper'])
        whisper_entry = ttk.Entry(thresholds_grid, textvariable=self.whisper_thresh, width=6)
        whisper_entry.grid(row=0, column=3, padx=1, pady=1)
        whisper_entry.bind("<Return>", lambda e: self.update_thresholds())
        whisper_entry.bind("<FocusOut>", lambda e: self.update_thresholds())
        
        ttk.Label(thresholds_grid, text="Норма:").grid(row=1, column=0, sticky="w", padx=1, pady=1)
        self.normal_thresh = tk.DoubleVar(value=self.thresholds['normal'])
        normal_entry = ttk.Entry(thresholds_grid, textvariable=self.normal_thresh, width=6)
        normal_entry.grid(row=1, column=1, padx=1, pady=1)
        normal_entry.bind("<Return>", lambda e: self.update_thresholds())
        normal_entry.bind("<FocusOut>", lambda e: self.update_thresholds())
        
        ttk.Label(thresholds_grid, text="Крик:").grid(row=1, column=2, sticky="w", padx=1, pady=1)
        self.shout_thresh = tk.DoubleVar(value=self.thresholds['shout'])
        shout_entry = ttk.Entry(thresholds_grid, textvariable=self.shout_thresh, width=6)
        shout_entry.grid(row=1, column=3, padx=1, pady=1)
        shout_entry.bind("<Return>", lambda e: self.update_thresholds())
        shout_entry.bind("<FocusOut>", lambda e: self.update_thresholds())
        
        ttk.Button(thresholds_grid, text="Применить", command=self.update_thresholds).grid(
            row=2, column=0, columnspan=4, pady=3, sticky="ew")
        
        help_label = ttk.Label(
            thresholds_grid,
            text="Значения: 0.0-1.0 (0=мин, 1=макс громкость)",
            font=("Arial", 7)
        )
        help_label.grid(row=3, column=0, columnspan=4, pady=(0, 1))

        # Активные состояния голоса (сворачиваемые)
        states_frame = ttk.Frame(self.expand_content)
        states_frame.pack(fill="x", pady=(0, 3))
        states_header = ttk.Frame(states_frame)
        states_header.pack(fill="x")
        
        self.states_expanded = self.sections_state.get('states', True)
        states_text = "▼ Активные состояния" if self.states_expanded else "▶ Активные состояния"
        self.states_header_label = ttk.Label(states_header, text=states_text, font=("Arial", 9, "bold"), cursor="hand2")
        self.states_header_label.pack(side="left", padx=2, pady=2)
        states_header.bind("<Button-1>", lambda e: self.toggle_section("states"))
        self.states_header_label.bind("<Button-1>", lambda e: self.toggle_section("states"))
        self.states_content = ttk.Frame(states_frame)
        
        if self.states_expanded:
            self.states_content.pack(fill="x", padx=3, pady=(0, 2))

        self.state_vars = {
            'silent': tk.BooleanVar(value=self.settings.get('active_states', {}).get('silent', True)),
            'whisper': tk.BooleanVar(value=self.settings.get('active_states', {}).get('whisper', True)),
            'normal': tk.BooleanVar(value=self.settings.get('active_states', {}).get('normal', True)),
            'shout': tk.BooleanVar(value=self.settings.get('active_states', {}).get('shout', True))
        }

        states_grid = ttk.Frame(self.states_content)
        states_grid.pack(fill="x", padx=2, pady=2)
        
        ttk.Checkbutton(states_grid, text="Тишина", variable=self.state_vars['silent'],
                       command=self.update_active_states).grid(row=0, column=0, sticky="w", padx=3, pady=1)
        ttk.Checkbutton(states_grid, text="Шёпот", variable=self.state_vars['whisper'],
                       command=self.update_active_states).grid(row=0, column=1, sticky="w", padx=3, pady=1)
        ttk.Checkbutton(states_grid, text="Норма", variable=self.state_vars['normal'],
                       command=self.update_active_states).grid(row=1, column=0, sticky="w", padx=3, pady=1)
        ttk.Checkbutton(states_grid, text="Крик", variable=self.state_vars['shout'],
                       command=self.update_active_states).grid(row=1, column=1, sticky="w", padx=3, pady=1)

        # Глобальные эффекты (сворачиваемые)
        effects_frame = ttk.Frame(self.expand_content)
        effects_frame.pack(fill="x", pady=(0, 3))
        effects_header = ttk.Frame(effects_frame)
        effects_header.pack(fill="x")
        
        self.effects_expanded = self.sections_state.get('effects', True)
        effects_text = "▼ Глобальные эффекты" if self.effects_expanded else "▶ Глобальные эффекты"
        self.effects_header_label = ttk.Label(effects_header, text=effects_text, font=("Arial", 9, "bold"), cursor="hand2")
        self.effects_header_label.pack(side="left", padx=2, pady=2)
        effects_header.bind("<Button-1>", lambda e: self.toggle_section("effects"))
        self.effects_header_label.bind("<Button-1>", lambda e: self.toggle_section("effects"))
        self.effects_content = ttk.Frame(effects_frame)
        
        if self.effects_expanded:
            self.effects_content.pack(fill="x", padx=3, pady=(0, 2))
        
        effects_grid = ttk.Frame(self.effects_content)
        effects_grid.pack(fill="x", padx=2, pady=2)
        
        self.shake = tk.BooleanVar(value=self.effects.get('shake', False))
        ttk.Checkbutton(effects_grid, text="Дрожание", variable=self.shake,
                       command=self.update_effects).pack(anchor="w", padx=3, pady=1)
        
        self.bounce = tk.BooleanVar(value=self.effects.get('bounce', False))
        ttk.Checkbutton(effects_grid, text="Прыжки", variable=self.bounce,
                       command=self.update_effects).pack(anchor="w", padx=3, pady=1)
        
        self.pulse = tk.BooleanVar(value=self.effects.get('pulse', False))
        ttk.Checkbutton(effects_grid, text="Пульсация", variable=self.pulse,
                       command=self.update_effects).pack(anchor="w", padx=3, pady=1)
        
        self.blink = tk.BooleanVar(value=self.effects.get('blink', True))
        ttk.Checkbutton(effects_grid, text="Моргание", variable=self.blink,
                       command=self.update_effects).pack(anchor="w", padx=3, pady=1)
        
        self.random_effect = tk.BooleanVar(value=self.effects.get('random_effect', False))
        ttk.Checkbutton(effects_grid, text="Случайная смена", variable=self.random_effect,
                       command=self.update_effects).pack(anchor="w", padx=3, pady=1)
        
        # Эффект "Волна" (переименовано с "Водная рябь")
        self.wave = tk.BooleanVar(value=self.effects.get('wave', False))
        ttk.Checkbutton(effects_grid, text="Волна", variable=self.wave,
                       command=self.update_effects).pack(anchor="w", padx=3, pady=1)

        # Настройки эффекта "Волна" (появляются только при включении)
        self.wave_settings_frame = ttk.Frame(effects_grid)
        self.wave_settings_frame.pack(fill="x", padx=10, pady=(0, 3))

        # Амплитуда с шагом 0.25 и отображением значения
        amplitude_frame = ttk.Frame(self.wave_settings_frame)
        amplitude_frame.pack(fill="x", padx=3, pady=(2, 0))
        ttk.Label(amplitude_frame, text="Сила (0.5-10.0):").pack(anchor="w", side="left")
        self.wave_amplitude = tk.DoubleVar(value=self._round_to_step(self.wave_params.get('amplitude', 3.0), 0.25))
        self.wave_amplitude_label = ttk.Label(amplitude_frame, text=f"{self.wave_amplitude.get():.2f}")
        self.wave_amplitude_label.pack(anchor="e", side="right", padx=5)
        wave_amp_scale = ttk.Scale(amplitude_frame, from_=0.5, to=10.0, 
                                  variable=self.wave_amplitude, orient="horizontal", length=150)
        wave_amp_scale.pack(fill="x", padx=3, pady=(0, 2))
        wave_amp_scale.configure(command=lambda val: self._on_wave_scale_move('amplitude', val))
        wave_amp_scale.bind("<ButtonRelease-1>", lambda e: self.update_wave_params())

        # Частота с шагом 0.25 и отображением значения
        frequency_frame = ttk.Frame(self.wave_settings_frame)
        frequency_frame.pack(fill="x", padx=3, pady=(2, 0))
        ttk.Label(frequency_frame, text="Частота (0.1-2.0):").pack(anchor="w", side="left")
        self.wave_frequency = tk.DoubleVar(value=self._round_to_step(self.wave_params.get('frequency', 0.5), 0.25))
        self.wave_frequency_label = ttk.Label(frequency_frame, text=f"{self.wave_frequency.get():.2f}")
        self.wave_frequency_label.pack(anchor="e", side="right", padx=5)
        wave_freq_scale = ttk.Scale(frequency_frame, from_=0.1, to=2.0, 
                                   variable=self.wave_frequency, orient="horizontal", length=150)
        wave_freq_scale.pack(fill="x", padx=3, pady=(0, 2))
        wave_freq_scale.configure(command=lambda val: self._on_wave_scale_move('frequency', val))
        wave_freq_scale.bind("<ButtonRelease-1>", lambda e: self.update_wave_params())

        # Скорость с шагом 0.25 и отображением значения
        speed_frame = ttk.Frame(self.wave_settings_frame)
        speed_frame.pack(fill="x", padx=3, pady=(2, 0))
        ttk.Label(speed_frame, text="Скорость (0.1-3.0):").pack(anchor="w", side="left")
        self.wave_speed = tk.DoubleVar(value=self._round_to_step(self.wave_params.get('speed', 1.0), 0.25))
        self.wave_speed_label = ttk.Label(speed_frame, text=f"{self.wave_speed.get():.2f}")
        self.wave_speed_label.pack(anchor="e", side="right", padx=5)
        wave_speed_scale = ttk.Scale(speed_frame, from_=0.1, to=3.0, 
                                    variable=self.wave_speed, orient="horizontal", length=150)
        wave_speed_scale.pack(fill="x", padx=3, pady=(0, 2))
        wave_speed_scale.configure(command=lambda val: self._on_wave_scale_move('speed', val))
        wave_speed_scale.bind("<ButtonRelease-1>", lambda e: self.update_wave_params())

        # Показываем/скрываем настройки в зависимости от состояния чекбокса
        self._update_wave_ui()
        self.wave.trace('w', lambda *args: self._update_wave_ui())

        # Настройки idle-режима (сворачиваемые)
        idle_frame = ttk.Frame(self.expand_content)
        idle_frame.pack(fill="x", pady=(0, 3))
        idle_header = ttk.Frame(idle_frame)
        idle_header.pack(fill="x")
        
        self.idle_expanded = self.sections_state.get('idle', True)
        idle_text = "▼ Idle-режим" if self.idle_expanded else "▶ Idle-режим"
        self.idle_header_label = ttk.Label(idle_header, text=idle_text, font=("Arial", 9, "bold"), cursor="hand2")
        self.idle_header_label.pack(side="left", padx=2, pady=2)
        idle_header.bind("<Button-1>", lambda e: self.toggle_section("idle"))
        self.idle_header_label.bind("<Button-1>", lambda e: self.toggle_section("idle"))
        self.idle_content = ttk.Frame(idle_frame)
        
        if self.idle_expanded:
            self.idle_content.pack(fill="x", padx=3, pady=(0, 2))

        idle_grid = ttk.Frame(self.idle_content)
        idle_grid.pack(fill="x", padx=2, pady=2)
        
        self.idle_enabled = tk.BooleanVar(value=self.settings.get('idle_enabled', False))
        ttk.Checkbutton(idle_grid, text="Включить затемнение в idle", variable=self.idle_enabled,
                       command=self.update_idle_setting).pack(anchor="w", padx=3, pady=1)

        ttk.Label(idle_grid, text="Время до затемнения (сек):").pack(anchor="w", padx=3, pady=(3, 0))
        self.idle_timeout = tk.DoubleVar(value=self.settings.get('idle_timeout', 60.0))
        idle_entry = ttk.Entry(idle_grid, textvariable=self.idle_timeout, width=10)
        idle_entry.pack(anchor="w", padx=3, pady=(0, 2))
        idle_entry.bind("<Return>", lambda e: self.update_idle_setting())
        idle_entry.bind("<FocusOut>", lambda e: self.update_idle_setting())

        # Запуск обработки аудио
        self.audio.start()
        self.toggle_noise_gate()

        # Настраиваем рендерер, но НЕ запускаем его
        self.renderer.set_thresholds(self.thresholds)
        self.renderer.set_noise_gate(self.noise_gate_threshold.get() if self.noise_gate_enabled.get() else 0.0)
        self.renderer.set_idle(self.idle_enabled.get(), self.idle_timeout.get())

        # Применение начальных состояний
        self.update_active_states()

        # Обновление визуализации порогов
        self.update_threshold_visuals()

        # Обновление слотов
        self.refresh_slot_buttons()

        # Если в настройках есть текущий слот, загружаем его
        if self.current_slot:
            self.load_slot(self.current_slot - 1, silent=True)  # -1 потому что индексация с 0
            
        # Завершаем инициализацию
        self.initializing = False

    def load_preview_for_slot(self, slot_idx):
        """Загрузка превью для слота"""
        preview_path = os.path.join(MODELS_DIR, f"slot{slot_idx+1}", "preview.png")
        
        if os.path.exists(preview_path):
            try:
                img = Image.open(preview_path)
                img.thumbnail((85, 85), Image.Resampling.LANCZOS)
                photo = ImageTk.PhotoImage(img)
                self.slot_previews[slot_idx] = photo
                return photo
            except Exception as e:
                logger.error(f"Error loading preview for slot {slot_idx+1}: {e}")
                # В случае ошибки возвращаем пустое изображение
                img = Image.new("RGBA", (85, 85), (0, 0, 0, 0))
                photo = ImageTk.PhotoImage(img)
                self.slot_previews[slot_idx] = photo
                return photo
        
        # Если превью нет, возвращаем пустое изображение
        img = Image.new("RGBA", (85, 85), (0, 0, 0, 0))
        photo = ImageTk.PhotoImage(img)
        self.slot_previews[slot_idx] = photo
        return photo

    def show_temporary_message(self, title, message, duration_ms=3000):
        """Показать временное сообщение, которое исчезнет через указанное время"""
        # Создаем всплывающее окно
        popup = tk.Toplevel(self.root)
        popup.title(title)
        popup.transient(self.root)  # Делаем окно зависимым от главного
        popup.resizable(False, False)
        
        # Центрируем окно
        popup.geometry("+%d+%d" % (
            self.root.winfo_rootx() + self.root.winfo_width() // 2 - 150,
            self.root.winfo_rooty() + self.root.winfo_height() // 2 - 50
        ))
        
        # Добавляем текст сообщения
        message_label = ttk.Label(popup, text=message, padding=10)
        message_label.pack()
        
        # Автоматическое закрытие через указанное время
        popup.after(duration_ms, popup.destroy)
        
        # Даем фокус главному окну
        self.root.focus_set()

    def on_audio_level(self, level):
        """Оптимизированная обработка уровня аудио"""
        now = time.time()
        
        # Троттлинг обновления UI (максимум 20 FPS)
        if now - self._last_ui_update < 0.05:  # 50ms
            # Только передаем в рендерер если он запущен
            if self.renderer_was_started:
                self.renderer.set_audio_level(level * self.sensitivity.get())
            return
            
        try:
            self.audio_level_scaled = level * self.sensitivity.get()
            
            # Пакетное обновление UI
            self.root.after_idle(self._batch_ui_update, self.audio_level_scaled)
            
            # Передаем в рендерер только если он запущен
            if self.renderer_was_started:
                self.renderer.set_audio_level(self.audio_level_scaled)
            
            self._last_ui_update = now
        except Exception as e:
            logger.error(f"Audio level error: {e}")
    
    def _batch_ui_update(self, level):
        """Пакетное обновление UI элементов"""
        try:
            self.vol_label.config(text=f"{level:.2f}")
            self.update_level_indicator(level)
        except:
            pass  # Игнорируем ошибки в UI обновлении

    def refresh_slot_buttons(self):
        """Оптимизированное обновление кнопок слотов"""
        for idx in range(6):
            if idx < len(self.model_slots):
                self._update_single_slot(idx)

    def _update_single_slot(self, idx):
        """Обновление одного слота"""
        def update():
            try:
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
                        btn.config(text=f"{prefix}Слот {idx+1}\n{model_name[:15]}")
                    except:
                        btn.config(text=f"{prefix}Слот {idx+1}\n(ошибка)")
                else:
                    btn.config(text=f"{prefix}Слот {idx+1}\n(пустой)")
                
                # Обновляем превью - всегда загружаем заново
                photo = self.load_preview_for_slot(idx)
                btn.config(image=photo)
                btn.photo = photo  # Сохраняем ссылку
                
            except Exception as e:
                logger.debug(f"Slot update error: {e}")
        
        # Отложенное обновление
        self.root.after(idx * 50, update)

    def open_web_link(self):
        """Открытие ссылки веб-сервера в браузере"""
        import webbrowser
        try:
            url = "http://localhost:6969/"
            webbrowser.open(url)
            logger.info(f"Opened web link: {url}")
        except Exception as e:
            logger.error(f"Error opening web link: {e}")
            messagebox.showerror("Ошибка", f"Не удалось открыть ссылку: {e}")

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

    def _on_wave_scale_move(self, param, value):
        """Обработка движения шкалы эффекта 'Волна'"""
        # Округляем до шага 0.25
        rounded_value = self._round_to_step(float(value), 0.25)
        
        if param == 'amplitude':
            self.wave_amplitude.set(rounded_value)
            self.wave_amplitude_label.config(text=f"{rounded_value:.2f}")
        elif param == 'frequency':
            self.wave_frequency.set(rounded_value)
            self.wave_frequency_label.config(text=f"{rounded_value:.2f}")
        elif param == 'speed':
            self.wave_speed.set(rounded_value)
            self.wave_speed_label.config(text=f"{rounded_value:.2f}")

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
        self.save_settings()  # Автоматическое сохранение

    def on_sensitivity_change(self):
        """Изменение чувствительности"""
        self.audio.set_sensitivity(self.sensitivity.get())
        logger.info(f"Sensitivity changed to: {self.sensitivity.get()}")
        self.save_settings()  # Автоматическое сохранение

    def toggle_noise_gate(self):
        """Переключение подавления шума"""
        enabled = self.noise_gate_enabled.get()
        threshold = self.noise_gate_threshold.get() if enabled else 0.0
        self.audio.noise_gate_threshold = threshold
        self.renderer.set_noise_gate(threshold)
        logger.info(f"Noise gate {'enabled' if enabled else 'disabled'} with threshold: {threshold}")
        self.save_settings()  # Автоматическое сохранение

    def update_noise_gate_threshold(self):
        """Обновление порога подавления шума"""
        if self.noise_gate_enabled.get():
            threshold = self.noise_gate_threshold.get()
            self.audio.noise_gate_threshold = threshold
            self.renderer.set_noise_gate(threshold)
            logger.info(f"Noise gate threshold updated to: {threshold}")
        self.save_settings()  # Автоматическое сохранение

    def _update_wave_ui(self):
        """Показывает/скрывает настройки эффекта 'Волна'"""
        if self.wave.get():
            self.wave_settings_frame.pack(fill="x", padx=10, pady=(0, 3))
        else:
            self.wave_settings_frame.pack_forget()

    def update_wave_params(self):
        """Обновляет параметры эффекта 'Волна'"""
        self.wave_params = {
            'amplitude': self.wave_amplitude.get(),
            'frequency': self.wave_frequency.get(),
            'speed': self.wave_speed.get()
        }
        
        if self.wave.get():
            self.renderer.set_wave(
                True,
                self.wave_params['amplitude'],
                self.wave_params['frequency'],
                self.wave_params['speed']
            )
        
        self.save_settings()

    def update_effects(self):
        """Обновление эффектов"""
        effects = {
            'shake': self.shake.get(),
            'bounce': self.bounce.get(),
            'pulse': self.pulse.get(),
            'blink': self.blink.get(),
            'random_effect': self.random_effect.get(),
            'wave': self.wave.get()  # Переименовано
        }
        self.renderer.set_effects(effects)
        
        # Обновляем параметры эффекта 'Волна' если он включен
        if self.wave.get():
            self.update_wave_params()
        
        logger.info(f"Effects updated: {effects}")
        self.save_settings()  # Автоматическое сохранение

    def update_idle_setting(self):
        """Обновление настройки idle-режима"""
        enabled = self.idle_enabled.get()
        timeout = self.idle_timeout.get()
        self.renderer.set_idle(enabled, timeout)
        logger.info(f"Idle mode updated: enabled={enabled}, timeout={timeout}")
        self.save_settings()  # Автоматическое сохранение

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
        """Сохранение настроек (автоматическое, без сообщения)"""
        if self.initializing:
            return  # Не сохраняем во время инициализации
            
        settings = {
            'thresholds': self.thresholds,
            'active_states': {state: var.get() for state, var in self.state_vars.items()},
            'effects': {
                'shake': self.shake.get(),
                'bounce': self.bounce.get(),
                'pulse': self.pulse.get(),
                'blink': self.blink.get(),
                'random_effect': self.random_effect.get(),
                'wave': self.wave.get()  # Переименовано
            },
            'wave_params': self.wave_params,  # Переименовано
            'sensitivity': self.sensitivity.get(),
            'noise_gate_enabled': self.noise_gate_enabled.get(),
            'noise_gate_threshold': self.noise_gate_threshold.get(),
            'mic_device': self.device_var.get(),
            'idle_enabled': self.idle_enabled.get(),
            'idle_timeout': self.idle_timeout.get(),
            'current_slot': self.current_slot,
            'sections_state': {  # Сохраняем состояние раскрытия секций
                'thresh': self.thresh_expanded,
                'states': self.states_expanded,
                'effects': self.effects_expanded,
                'idle': self.idle_expanded
            }
        }
        try:
            with open(SETTINGS_FILE, 'w') as f:
                json.dump(settings, f, indent=2)
            logger.info("Settings saved automatically")
        except Exception as e:
            logger.error(f"Error saving settings: {e}")
    
    def toggle_section(self, section_name):
        """Сворачивание/разворачивание секций настроек"""
        if section_name == "thresh":
            if self.thresh_expanded:
                self.thresh_content.pack_forget()
                self.thresh_expanded = False
                self.thresh_header_label.config(text="▶ Пороги голоса")
            else:
                self.thresh_content.pack(fill="x", padx=3, pady=(0, 2))
                self.thresh_expanded = True
                self.thresh_header_label.config(text="▼ Пороги голоса")
        elif section_name == "states":
            if self.states_expanded:
                self.states_content.pack_forget()
                self.states_expanded = False
                self.states_header_label.config(text="▶ Активные состояния")
            else:
                self.states_content.pack(fill="x", padx=3, pady=(0, 2))
                self.states_expanded = True
                self.states_header_label.config(text="▼ Активные состояния")
        elif section_name == "effects":
            if self.effects_expanded:
                self.effects_content.pack_forget()
                self.effects_expanded = False
                self.effects_header_label.config(text="▶ Глобальные эффекты")
            else:
                self.effects_content.pack(fill="x", padx=3, pady=(0, 2))
                self.effects_expanded = True
                self.effects_header_label.config(text="▼ Глобальные эффекты")
        elif section_name == "idle":
            if self.idle_expanded:
                self.idle_content.pack_forget()
                self.idle_expanded = False
                self.idle_header_label.config(text="▶ Idle-режим")
            else:
                self.idle_content.pack(fill="x", padx=3, pady=(0, 2))
                self.idle_expanded = True
                self.idle_header_label.config(text="▼ Idle-режим")
        
        # Автоматическое сохранение состояния секций
        self.save_settings()

    def update_active_states(self):
        """Обновление активных состояний"""
        active_states = {}
        for state, var in self.state_vars.items():
            active_states[state] = var.get()
        self.renderer.set_active_states(active_states)
        self.update_threshold_visuals()  # Обновляем визуализацию порогов
        logger.info(f"Active states updated: {active_states}")
        self.save_settings()  # Автоматическое сохранение

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
        self.save_settings()  # Автоматическое сохранение

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
                self.level_canvas.coords(self.threshold_lines[key], -10, 0, -10, 25)
                continue
                
            try:
                val = float(self.thresholds[key])
            except Exception:
                val = 0.0
            pos = min(1.0, max(0.0, val)) * canvas_width
            self.level_canvas.coords(self.threshold_lines[key], pos, 0, pos, 25)
            anchor = "center"
            if key == "silent":
                anchor = "e"
            elif key == "shout":
                anchor = "w"
            self.level_canvas.create_text(
                pos, 7,
                text=key,
                anchor=anchor,
                tags="threshold_label",
                font=("Arial", 7)
            )

    def update_level_indicator(self, level):
        """Обновление индикатора уровня"""
        canvas_width = self.level_canvas.winfo_width()
        if canvas_width < 10:
            return
        level_clamped = min(1.0, max(0.0, float(level)))
        indicator_width = level_clamped * canvas_width
        try:
            self.level_canvas.coords(self.level_indicator, 0, 0, indicator_width, 25)
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

    def load_slot(self, idx, silent=False):
        """Загрузка модели из слота"""
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
            self.renderer.load_model(data, slot_dir)
            if self.webserver:
                self.webserver.renderer = self.renderer

        # Устанавливаем текущий слот
        self.current_slot = idx + 1
        
        # Обновляем кнопки слотов и превью
        self.refresh_slot_buttons()
        
        # Сохраняем настройки
        self.save_settings()

        model_name = self.renderer.model.get('name','модель')
        
        if not silent:
            logger.info(f"Model loaded from slot {idx+1}: {model_name}")
            # Используем временное сообщение вместо стандартного messagebox
            self.show_temporary_message("Загружено", f"Модель загружена из слота {idx+1}")

    def open_editor(self):
        """Открытие редактора моделей"""
        try:
            main_window = self.root
            # ВАЖНО: Устанавливаем атрибут app для главного окна
            main_window.app = self
            main_window.attributes('-disabled', True)
            
            editor = ModelEditor(
                main_window, 
                on_save=self.on_model_saved,
                device=self.device_var.get(),
                noise_gate_threshold=self.noise_gate_threshold.get() if self.noise_gate_enabled.get() else 0.0,
                sensitivity=self.sensitivity.get(),
                thresholds=self.thresholds,
                current_slot=self.current_slot  # Передаем текущий слот в редактор
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
        """Обработка сохранения модели"""
        # Обновляем кнопки слотов
        self.refresh_slot_buttons()
        logger.info(f"Model saved to directory: {model_dir}")
        
        # Если сохранено в текущий слот, перезагружаем модель
        if slot_num == self.current_slot:
            logger.info(f"Model saved to current slot {slot_num}, reloading")
            self.load_slot(slot_num - 1, silent=True)  # -1 потому что индексация с 0

    def toggle_server(self):
        """Переключение веб-сервера"""
        if self.webserver and getattr(self.webserver, "is_running", False):
            # Останавливаем веб-сервер
            self.webserver.stop()
            self.server_btn.config(text="🌐 Запустить веб-сервер")
            self.link_btn.config(state="disabled")
            logger.info("Web server stopped")
            
            # Останавливаем рендерер только если он был запущен для веб-сервера
            if self.renderer_was_started:
                try:
                    self.renderer.stop()
                    self.renderer_was_started = False
                    logger.info("Renderer stopped")
                except Exception as e:
                    logger.error(f"Error stopping renderer: {e}")
        else:
            if not self.webserver:
                self.webserver = WebServer(self.renderer)
            elif not self.webserver.is_running:
                self.webserver.renderer = self.renderer
            
            # Запускаем рендерер перед запуском веб-сервера
            if not self.renderer_was_started:
                try:
                    # Настраиваем параметры рендерера перед запуском
                    self.renderer.set_thresholds(self.thresholds)
                    self.renderer.set_noise_gate(self.noise_gate_threshold.get() if self.noise_gate_enabled.get() else 0.0)
                    self.renderer.set_idle(self.idle_enabled.get(), self.idle_timeout.get())
                    self.renderer.set_effects(self.effects)
                    
                    # Обновляем эффект "Волна"
                    if self.wave.get():
                        self.renderer.set_wave(
                            True,
                            self.wave_params['amplitude'],
                            self.wave_params['frequency'],
                            self.wave_params['speed']
                        )
                    
                    # Обновляем активные состояния
                    active_states = {state: var.get() for state, var in self.state_vars.items()}
                    self.renderer.set_active_states(active_states)
                    
                    # Запускаем рендерер
                    self.renderer.start()
                    self.renderer_was_started = True
                    logger.info("Renderer started for web server")
                except Exception as e:
                    logger.error(f"Error starting renderer: {e}")
            
            try:
                self.webserver.start()
                self.server_btn.config(text="⏹️ Остановить веб-сервер")
                self.link_btn.config(state="normal")
                logger.info("Web server started")
            except Exception as e:
                logger.error(f"Error starting web server: {e}")
                messagebox.showerror("Ошибка", f"Не удалось запустить веб-сервер: {e}")

    def on_close(self):
        """Обработка закрытия приложения"""
        try:
            self.audio.stop()
        except Exception as e:
            logger.error(f"Error stopping audio: {e}")
        
        # Останавливаем рендерер только если он был запущен
        if self.renderer_was_started:
            try:
                self.renderer.stop()
            except Exception as e:
                logger.error(f"Error stopping renderer: {e}")
        
        if self.webserver:
            try:
                self.webserver.stop()
            except Exception as e:
                logger.error(f"Error stopping web server: {e}")
        
        # Отключаем кнопку ссылки при закрытии
        self.link_btn.config(state="disabled")
        
        self.save_settings()  # Сохраняем настройки при закрытии
        logger.info("Application closed")
        self.root.destroy()

if __name__ == "__main__":
    root = tk.Tk()
    app = App(root)
    root.mainloop()