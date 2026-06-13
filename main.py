# main.py
import threading
import time
import tkinter as tk
from tkinter import ttk, messagebox
from editor import ModelEditor
from renderer import Renderer
from webserver import WebServer
from audio import AudioProcessor, list_host_apis, list_audio_devices
import os
import json
from PIL import Image, ImageTk
import sounddevice as sd
import sys
import psutil
import socket

# Определение базовой директории
if getattr(sys, 'frozen', False):
    BASE_DIR = os.path.dirname(sys.executable)
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# Импортируем логирование из utils
from utils import setup_logging
logger = setup_logging('main')

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
        root.minsize(800, 470)

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

        # Загружаем порт из настроек (по умолчанию 6969)
        self.webserver_port = self.settings.get('webserver_port', 6969)

        # Загружаем выбранный Host API (новое)
        self.host_api_index = self.settings.get(
            'host_api_index')  # может быть None

        # Инициализация компонентов
        self.renderer = Renderer(width=700, height=700, fps=60)
        self.audio = AudioProcessor(callback=self.on_audio_level,
                                    device=self.settings.get('mic_device', ''),
                                    host_api_index=self.host_api_index)
        self.audio.noise_gate_threshold = self.settings.get(
            'noise_gate_threshold', 0.01)
        self.webserver = None
        self.renderer_was_started = False  # Флаг для отслеживания запуска рендерера

        # Глобальные эффекты
        self.effects = self.settings.get('effects', {
            'shake': False,
            'bounce': False,
            'pulse': False,
            'blink': True,
            'random_effect': True,
            'wave': False
        })

        # Параметры эффекта "Волна"
        self.wave_params = self.settings.get('wave_params', {
            'amplitude': 7.0,
            'frequency': 1.0,
            'speed': 3
        })

        self.renderer.set_effects(self.effects)
        # Устанавливаем параметры эффекта "Волна" (только если он включен в effects)
        if self.effects.get('wave', False):
            self.renderer.set_wave(
                True,
                self.wave_params.get('amplitude', 7.0),
                self.wave_params.get('frequency', 1.0),
                self.wave_params.get('speed', 3.0)
            )

        # Состояние раскрытия секций (по умолчанию все открыты)
        self.sections_state = self.settings.get('sections_state', {
            'effects': True,
            'wave': True,
            'idle': True,
            'thresh': True,
            'states': True
        })

        # UI layout - три колонки в ряд
        main_frame = ttk.Frame(root, padding=3)
        main_frame.pack(fill="both", expand=True)

        # Настройка весов колонок
        # Модели - фиксированная ширина
        main_frame.columnconfigure(0, weight=0)
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
        # Загружаем текущий слот из настроек
        self.current_slot = self.settings.get('current_slot')

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

        # Кнопка изменения порта
        self.port_btn = ttk.Button(
            control_frame,
            text=f"Порт: {self.webserver_port}",
            command=self.change_port
        )
        self.port_btn.pack(fill="x", padx=2, pady=2)

        # Настройки микрофона
        mic_frame = ttk.LabelFrame(settings_frame, text="🎤 Микрофон")
        mic_frame.pack(fill="x", pady=(0, 3), padx=3)

        # === НОВОЕ: выбор Host API ===
        ttk.Label(mic_frame, text="Аудио API:").pack(
            anchor='w', padx=2, pady=(2, 0))
        api_row = ttk.Frame(mic_frame)
        api_row.pack(fill='x', padx=2, pady=(0, 2))
        self.host_api_combo = ttk.Combobox(api_row, state="readonly", width=20)
        self.host_api_combo.pack(side='left', fill='x', expand=True)
        self.host_api_combo.bind(
            '<<ComboboxSelected>>', self.on_host_api_change)

        # Выбор устройства
        ttk.Label(mic_frame, text="Устройство:").pack(
            anchor='w', padx=2, pady=(2, 0))
        device_row = ttk.Frame(mic_frame)
        device_row.pack(fill='x', padx=2, pady=(0, 2))

        self.device_var = tk.StringVar(
            value=self.settings.get('mic_device', ''))
        self.device_combo = ttk.Combobox(
            device_row, textvariable=self.device_var, width=18)
        self.device_combo.pack(side='left', fill='x', expand=True)

        # Кнопка обновления списка устройств
        refresh_btn = ttk.Button(device_row, text="↻",
                                 width=3, command=self.refresh_devices)
        refresh_btn.pack(side='left', padx=(2, 0))

        # Заполнение устройств (будет вызвано после инициализации)
        self.device_combo.bind('<<ComboboxSelected>>', self.on_device_change)

        # Заполняем список Host API и устройств (новое)
        self.refresh_host_apis()
        self.refresh_devices()

        ttk.Label(mic_frame, text="Уровень:").pack(
            anchor="w", padx=2, pady=(2, 0))
        self.vol_label = ttk.Label(mic_frame, text="0.00")
        self.vol_label.pack(anchor="w", padx=2, pady=(0, 2))

        # Чувствительность с шагом 5%
        sens_frame = ttk.Frame(mic_frame)
        sens_frame.pack(fill="x", padx=2, pady=0)

        ttk.Label(sens_frame, text="Чувствительность:").pack(
            anchor="w", side="left")
        self.sensitivity = tk.DoubleVar(value=self._round_to_step(
            self.settings.get('sensitivity', 1.5), 0.05))
        self.sens_percent_label = ttk.Label(
            sens_frame, text=f"{self.sensitivity.get()*100:.0f}%")
        self.sens_percent_label.pack(anchor="e", side="right")

        # Шкала чувствительности с шагом 5% (0.05)
        sens_scale = ttk.Scale(mic_frame, from_=0.1, to=5.0,
                               variable=self.sensitivity, orient="horizontal", length=180)
        sens_scale.pack(fill="x", padx=2, pady=1)
        sens_scale.configure(command=self._on_sensitivity_scale_move)
        sens_scale.bind("<ButtonRelease-1>",
                        lambda e: self.on_sensitivity_change())

        # Подавление шума с настройкой мощности и шагом 0.005
        noise_gate_frame = ttk.Frame(mic_frame)
        noise_gate_frame.pack(fill="x", padx=2, pady=0)

        self.noise_gate_enabled = tk.BooleanVar(
            value=self.settings.get('noise_gate_enabled', True))
        ttk.Checkbutton(noise_gate_frame, text="Подавление шума", variable=self.noise_gate_enabled,
                        command=self.toggle_noise_gate).pack(side="left")

        # Текущее значение подавления шума
        self.noise_gate_value_label = ttk.Label(noise_gate_frame, text="0.010")
        self.noise_gate_value_label.pack(side="right", padx=2)

        self.noise_gate_threshold = tk.DoubleVar(value=self._round_to_step(
            self.settings.get('noise_gate_threshold', 0.01), 0.005))

        # Шкала подавления шума с шагом 0.005
        noise_gate_scale = ttk.Scale(mic_frame, from_=0.001, to=0.05, variable=self.noise_gate_threshold,
                                     orient="horizontal", length=180)
        noise_gate_scale.pack(fill="x", padx=2, pady=1)
        noise_gate_scale.configure(command=self._on_noise_gate_scale_move)
        noise_gate_scale.bind("<ButtonRelease-1>",
                              lambda e: self.update_noise_gate_threshold())

        # Индикатор уровня
        ttk.Label(mic_frame, text="Индикатор:").pack(
            anchor="w", padx=2, pady=(3, 0))
        self.level_canvas = tk.Canvas(
            mic_frame, width=180, height=25, bg="#f0f0f0")
        self.level_canvas.bind("<Configure>", self.on_canvas_resize)
        self.level_canvas.pack(fill="x", padx=2, pady=(0, 3))

        # ---- КОЛОНКА 3: Расширенные настройки ----
        expandable_frame = ttk.LabelFrame(
            main_frame, text="Расширенные настройки")
        expandable_frame.grid(
            row=0, column=2, sticky="nsew", padx=(3, 0), pady=0)

        # Создаем Canvas с прокруткой для третьей колонки
        self.expand_canvas = tk.Canvas(expandable_frame, bg="#f0f0f0")
        expand_scrollbar = ttk.Scrollbar(
            expandable_frame, orient="vertical", command=self.expand_canvas.yview)
        self.expand_canvas.configure(yscrollcommand=expand_scrollbar.set)

        expand_scrollbar.pack(side="right", fill="y")
        self.expand_canvas.pack(side="left", fill="both", expand=True)

        # Фрейм для содержимого внутри canvas
        self.expand_content = ttk.Frame(self.expand_canvas)
        self.expand_canvas.create_window(
            (0, 0), window=self.expand_content, anchor="nw", width=350)

        # Функция обновления прокрутки
        def configure_scrollregion(event):
            self.expand_canvas.configure(
                scrollregion=self.expand_canvas.bbox("all"))

        self.expand_content.bind("<Configure>", configure_scrollregion)

        # ---- Глобальные эффекты (сворачиваемые, первое место) ----
        effects_frame = ttk.Frame(self.expand_content)
        effects_frame.pack(fill="x", pady=(0, 3))
        effects_header = ttk.Frame(effects_frame)
        effects_header.pack(fill="x")

        self.effects_expanded = self.sections_state.get('effects', True)
        effects_text = "▼ Глобальные эффекты" if self.effects_expanded else "▶ Глобальные эффекты"
        self.effects_header_label = ttk.Label(
            effects_header, text=effects_text, font=("Arial", 9, "bold"), cursor="hand2")
        self.effects_header_label.pack(side="left", padx=2, pady=2)
        effects_header.bind(
            "<Button-1>", lambda e: self.toggle_section("effects"))
        self.effects_header_label.bind(
            "<Button-1>", lambda e: self.toggle_section("effects"))
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

        self.random_effect = tk.BooleanVar(
            value=self.effects.get('random_effect', True))
        ttk.Checkbutton(effects_grid, text="Случайная смена", variable=self.random_effect,
                        command=self.update_effects).pack(anchor="w", padx=3, pady=1)

        self.wave_effect = tk.BooleanVar(value=self.effects.get('wave', False))
        ttk.Checkbutton(effects_grid, text="Волна", variable=self.wave_effect,
                        command=self.update_effects).pack(anchor="w", padx=3, pady=1)

        # ---- Настройки эффекта "Волна" (второе место) ----
        wave_frame = ttk.Frame(self.expand_content)
        wave_frame.pack(fill="x", pady=(0, 3))
        wave_header = ttk.Frame(wave_frame)
        wave_header.pack(fill="x")

        self.wave_expanded = self.sections_state.get('wave', True)
        wave_text = "▼ Настройки Волны" if self.wave_expanded else "▶ Настройки Волны"
        self.wave_header_label = ttk.Label(
            wave_header, text=wave_text, font=("Arial", 9, "bold"), cursor="hand2")
        self.wave_header_label.pack(side="left", padx=2, pady=2)
        wave_header.bind("<Button-1>", lambda e: self.toggle_section("wave"))
        self.wave_header_label.bind(
            "<Button-1>", lambda e: self.toggle_section("wave"))
        self.wave_content = ttk.Frame(wave_frame)

        if self.wave_expanded:
            self.wave_content.pack(fill="x", padx=3, pady=(0, 2))

        wave_settings_grid = ttk.Frame(self.wave_content)
        wave_settings_grid.pack(fill="x", padx=2, pady=2)

        # Амплитуда с шагом 0.25 и отображением значения
        amplitude_frame = ttk.Frame(wave_settings_grid)
        amplitude_frame.pack(fill="x", padx=3, pady=(2, 0))
        ttk.Label(amplitude_frame,
                  text="Сила (0.5-10.0):").pack(anchor="w", side="left")
        self.wave_amplitude = tk.DoubleVar(value=self._round_to_step(
            self.wave_params.get('amplitude', 7.0), 0.25))
        self.wave_amplitude_label = ttk.Label(
            amplitude_frame, text=f"{self.wave_amplitude.get():.2f}")
        self.wave_amplitude_label.pack(anchor="e", side="right", padx=5)
        wave_amp_scale = ttk.Scale(amplitude_frame, from_=0.5, to=10.0,
                                   variable=self.wave_amplitude, orient="horizontal", length=150)
        wave_amp_scale.pack(fill="x", padx=3, pady=(0, 2))
        wave_amp_scale.configure(
            command=lambda val: self._on_wave_scale_move('amplitude', val))
        wave_amp_scale.bind("<ButtonRelease-1>",
                            lambda e: self.update_wave_params())

        # Частота с шагом 0.25 и отображением значения
        frequency_frame = ttk.Frame(wave_settings_grid)
        frequency_frame.pack(fill="x", padx=3, pady=(2, 0))
        ttk.Label(frequency_frame,
                  text="Частота (0.1-2.0):").pack(anchor="w", side="left")
        self.wave_frequency = tk.DoubleVar(value=self._round_to_step(
            self.wave_params.get('frequency', 1.0), 0.25))
        self.wave_frequency_label = ttk.Label(
            frequency_frame, text=f"{self.wave_frequency.get():.2f}")
        self.wave_frequency_label.pack(anchor="e", side="right", padx=5)
        wave_freq_scale = ttk.Scale(frequency_frame, from_=0.1, to=2.0,
                                    variable=self.wave_frequency, orient="horizontal", length=150)
        wave_freq_scale.pack(fill="x", padx=3, pady=(0, 2))
        wave_freq_scale.configure(
            command=lambda val: self._on_wave_scale_move('frequency', val))
        wave_freq_scale.bind("<ButtonRelease-1>",
                             lambda e: self.update_wave_params())

        # Скорость с шагом 1 и отображением значения (0-5)
        speed_frame = ttk.Frame(wave_settings_grid)
        speed_frame.pack(fill="x", padx=3, pady=(2, 0))
        ttk.Label(speed_frame, text="Скорость (0-5):").pack(anchor="w", side="left")
        self.wave_speed = tk.IntVar(
            value=int(self.wave_params.get('speed', 3)))
        self.wave_speed_label = ttk.Label(
            speed_frame, text=f"{self.wave_speed.get()}")
        self.wave_speed_label.pack(anchor="e", side="right", padx=5)
        wave_speed_scale = ttk.Scale(speed_frame, from_=0, to=5,
                                     variable=self.wave_speed, orient="horizontal", length=150)
        wave_speed_scale.pack(fill="x", padx=3, pady=(0, 2))
        wave_speed_scale.configure(
            command=lambda val: self._on_wave_scale_move('speed', val))
        wave_speed_scale.bind("<ButtonRelease-1>",
                              lambda e: self.update_wave_params())

        # ---- Idle-режим (третье место) ----
        idle_frame = ttk.Frame(self.expand_content)
        idle_frame.pack(fill="x", pady=(0, 3))
        idle_header = ttk.Frame(idle_frame)
        idle_header.pack(fill="x")

        self.idle_expanded = self.sections_state.get('idle', True)
        idle_text = "▼ Idle-режим" if self.idle_expanded else "▶ Idle-режим"
        self.idle_header_label = ttk.Label(
            idle_header, text=idle_text, font=("Arial", 9, "bold"), cursor="hand2")
        self.idle_header_label.pack(side="left", padx=2, pady=2)
        idle_header.bind("<Button-1>", lambda e: self.toggle_section("idle"))
        self.idle_header_label.bind(
            "<Button-1>", lambda e: self.toggle_section("idle"))
        self.idle_content = ttk.Frame(idle_frame)

        if self.idle_expanded:
            self.idle_content.pack(fill="x", padx=3, pady=(0, 2))

        idle_grid = ttk.Frame(self.idle_content)
        idle_grid.pack(fill="x", padx=2, pady=2)

        self.idle_enabled = tk.BooleanVar(
            value=self.settings.get('idle_enabled', True))
        ttk.Checkbutton(idle_grid, text="Включить затемнение в idle", variable=self.idle_enabled,
                        command=self.update_idle_setting).pack(anchor="w", padx=3, pady=1)

        ttk.Label(idle_grid, text="Время до затемнения (сек):").pack(
            anchor="w", padx=3, pady=(3, 0))
        self.idle_timeout = tk.DoubleVar(
            value=self.settings.get('idle_timeout', 5.0))
        idle_entry = ttk.Entry(
            idle_grid, textvariable=self.idle_timeout, width=10)
        idle_entry.pack(anchor="w", padx=3, pady=(0, 2))
        idle_entry.bind("<Return>", lambda e: self.update_idle_setting())
        idle_entry.bind("<FocusOut>", lambda e: self.update_idle_setting())

        # Время затухания (сек)
        ttk.Label(idle_grid, text="Время затухания (сек):").pack(
            anchor="w", padx=3, pady=(3, 0))
        self.idle_fade_duration = tk.DoubleVar(
            value=self.settings.get('idle_fade_duration', 0.3))
        fade_entry = ttk.Entry(
            idle_grid, textvariable=self.idle_fade_duration, width=10)
        fade_entry.pack(anchor="w", padx=3, pady=(0, 2))
        fade_entry.bind("<Return>", lambda e: self.update_idle_setting())
        fade_entry.bind("<FocusOut>", lambda e: self.update_idle_setting())

        # Время восстановления (сек)
        ttk.Label(idle_grid, text="Время восстановления (сек):").pack(
            anchor="w", padx=3, pady=(3, 0))
        self.idle_restore_duration = tk.DoubleVar(
            value=self.settings.get('idle_restore_duration', 0.1))
        restore_entry = ttk.Entry(
            idle_grid, textvariable=self.idle_restore_duration, width=10)
        restore_entry.pack(anchor="w", padx=3, pady=(0, 2))
        restore_entry.bind("<Return>", lambda e: self.update_idle_setting())
        restore_entry.bind("<FocusOut>", lambda e: self.update_idle_setting())

        # ---- Пороги голоса (четвертое место) ----
        thresh_frame = ttk.Frame(self.expand_content)
        thresh_frame.pack(fill="x", pady=(0, 3))
        thresh_header = ttk.Frame(thresh_frame)
        thresh_header.pack(fill="x")

        self.thresh_expanded = self.sections_state.get('thresh', True)
        thresh_text = "▼ Пороги голоса" if self.thresh_expanded else "▶ Пороги голоса"
        self.thresh_header_label = ttk.Label(
            thresh_header, text=thresh_text, font=("Arial", 9, "bold"), cursor="hand2")
        self.thresh_header_label.pack(side="left", padx=2, pady=2)
        thresh_header.bind(
            "<Button-1>", lambda e: self.toggle_section("thresh"))
        self.thresh_header_label.bind(
            "<Button-1>", lambda e: self.toggle_section("thresh"))
        self.thresh_content = ttk.Frame(thresh_frame)

        # Сетка для порогов будет создаваться динамически в refresh_thresholds_ui
        if self.thresh_expanded:
            self.thresh_content.pack(fill="x", padx=3, pady=(0, 2))

        # ---- Активные состояния (пятое место, в самом низу) ----
        states_frame = ttk.Frame(self.expand_content)
        states_frame.pack(fill="x", pady=(0, 3))
        states_header = ttk.Frame(states_frame)
        states_header.pack(fill="x")

        self.states_expanded = self.sections_state.get('states', True)
        states_text = "▼ Активные состояния" if self.states_expanded else "▶ Активные состояния"
        self.states_header_label = ttk.Label(
            states_header, text=states_text, font=("Arial", 9, "bold"), cursor="hand2")
        self.states_header_label.pack(side="left", padx=2, pady=2)
        states_header.bind(
            "<Button-1>", lambda e: self.toggle_section("states"))
        self.states_header_label.bind(
            "<Button-1>", lambda e: self.toggle_section("states"))
        self.states_content = ttk.Frame(states_frame)

        # UI активных состояний будет создаваться динамически в refresh_states_ui
        if self.states_expanded:
            self.states_content.pack(fill="x", padx=3, pady=(0, 2))

        # Запуск обработки аудио
        self.audio.start()
        self.toggle_noise_gate()

        # Настраиваем рендерер, но НЕ запускаем его
        self.renderer.set_noise_gate(
            self.noise_gate_threshold.get() if self.noise_gate_enabled.get() else 0.0)
        self.renderer.set_idle(
            self.idle_enabled.get(),
            self.idle_timeout.get(),
            self.idle_fade_duration.get(),
            self.idle_restore_duration.get()
        )

        # Создаём веб-сервер с нужным портом (но не запускаем)
        self.webserver = WebServer(self.renderer, port=self.webserver_port)

        # Обновление слотов
        self.refresh_slot_buttons()

        # Если в настройках есть текущий слот, загружаем его
        if self.current_slot:
            # -1 потому что индексация с 0
            self.load_slot(self.current_slot - 1, silent=True)

        # Завершаем инициализацию
        self.initializing = False

        self.root.after(100, self.on_canvas_resize)

    # === НОВЫЕ МЕТОДЫ ДЛЯ РАБОТЫ С HOST API ===
    def refresh_host_apis(self):
        """Обновляет список Host API в комбобоксе"""
        apis = list_host_apis()
        self.host_api_combo['values'] = [api['name'] for api in apis]
        self._host_apis_cache = apis  # сохраняем для быстрого доступа
        # Восстанавливаем выбранный API из настроек
        if self.host_api_index is not None:
            for idx, api in enumerate(apis):
                if api['index'] == self.host_api_index:
                    self.host_api_combo.current(idx)
                    break
            else:
                if apis:
                    self.host_api_combo.current(0)
                    self.host_api_index = apis[0]['index']
        else:
            # По умолчанию выбираем первый (обычно WASAPI на Windows)
            if apis:
                self.host_api_combo.current(0)
                self.host_api_index = apis[0]['index']
            else:
                self.host_api_combo.set('')
                self.host_api_index = None

    def on_host_api_change(self, event=None):
        """Обработчик смены Host API"""
        selection = self.host_api_combo.current()
        if selection >= 0 and hasattr(self, '_host_apis_cache') and selection < len(self._host_apis_cache):
            self.host_api_index = self._host_apis_cache[selection]['index']
        else:
            self.host_api_index = None
        # Обновляем список устройств
        self.refresh_devices()
        self.save_settings()  # сохраняем новый API

    # Модифицированный метод refresh_devices (раньше он назывался refresh_devices и был без учёта API)
    def refresh_devices(self):
        """Обновляет список доступных аудиоустройств с учётом выбранного Host API"""
        # Получаем список устройств для текущего API
        devices = list_audio_devices(host_api_index=self.host_api_index)
        current_device_name = self.device_var.get()
        self.device_combo['values'] = [dev['name'] for dev in devices]

        # Пытаемся сохранить выбранное устройство
        if current_device_name in self.device_combo['values']:
            self.device_var.set(current_device_name)
        else:
            self.device_var.set("По умолчанию")

        # Применяем новое устройство к аудиопроцессору
        selected_device_name = self.device_var.get()
        # Находим индекс устройства для sounddevice
        dev_index = None
        for dev in devices:
            if dev['name'] == selected_device_name:
                dev_index = dev['index']
                break
        self.audio.set_device_by_api(self.host_api_index, dev_index)

        # Если аудио уже запущено, перезапускаем
        was_running = self.audio.running
        if was_running:
            self.audio.stop()
            self.audio.start()
        logger.info(
            f"Devices refreshed, found {len(devices)} devices for API {self.host_api_index}")

    def on_device_change(self, event):
        """Смена аудиоустройства"""
        device_name = self.device_var.get()
        # Находим индекс устройства для текущего API
        devices = list_audio_devices(host_api_index=self.host_api_index)
        dev_index = None
        for dev in devices:
            if dev['name'] == device_name:
                dev_index = dev['index']
                break
        self.audio.set_device_by_api(self.host_api_index, dev_index)

        # Перезапускаем аудио
        was_running = self.audio.running
        if was_running:
            self.audio.stop()
            self.audio.start()
        logger.info(f"Audio device changed to: {device_name}")
        self.save_settings()  # Автоматическое сохранение

    # Остальные методы без изменений, за исключением save_settings, куда добавлен host_api_index
    def load_preview_for_slot(self, slot_idx):
        """Загрузка превью для слота"""
        preview_path = os.path.join(
            MODELS_DIR, f"slot{slot_idx+1}", "preview.png")

        if os.path.exists(preview_path):
            try:
                img = Image.open(preview_path)
                img.thumbnail((85, 85), Image.Resampling.LANCZOS)
                photo = ImageTk.PhotoImage(img)
                self.slot_previews[slot_idx] = photo
                return photo
            except Exception as e:
                logger.error(
                    f"Error loading preview for slot {slot_idx+1}: {e}")
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
            self.root.after_idle(self._batch_ui_update,
                                 self.audio_level_scaled)

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
                        btn.config(
                            text=f"{prefix}Слот {idx+1}\n{model_name[:15]}")
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
            url = f"http://localhost:{self.webserver_port}/"
            webbrowser.open(url)
            logger.info(f"Opened web link: {url}")
        except Exception as e:
            logger.error(f"Error opening web link: {e}")
            messagebox.showerror("Ошибка", f"Не удалось открыть ссылку: {e}")

    def change_port(self):
        """Изменение порта веб-сервера (из старой версии)"""
        if self.webserver and getattr(self.webserver, "is_running", False):
            messagebox.showwarning("Веб-сервер запущен",
                                   "Пожалуйста, остановите веб-сервер перед изменением порта.")
            return

        dialog = tk.Toplevel(self.root)
        dialog.title("Изменить порт веб-сервера")
        dialog.geometry("300x150")
        dialog.resizable(False, False)
        dialog.transient(self.root)
        dialog.grab_set()

        dialog.update_idletasks()
        x = self.root.winfo_x() + (self.root.winfo_width() // 2) - (300 // 2)
        y = self.root.winfo_y() + (self.root.winfo_height() // 2) - (150 // 2)
        dialog.geometry(f"+{x}+{y}")

        ttk.Label(dialog, text="Введите новый порт (1-65535):",
                  font=("Arial", 10)).pack(pady=(15, 5))

        port_frame = ttk.Frame(dialog)
        port_frame.pack(pady=10)

        port_var = tk.StringVar(value=str(self.webserver_port))
        port_entry = ttk.Entry(port_frame, textvariable=port_var, width=10,
                               font=("Arial", 12), justify="center")
        port_entry.pack(side="left", padx=5)
        port_entry.select_range(0, tk.END)
        port_entry.focus_set()

        def is_port_available(port):
            """Проверяет, свободен ли порт (не слушает ли его другой процесс)"""
            try:
                if port == self.webserver_port and not (self.webserver and self.webserver.is_running):
                    return True

                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.settimeout(0.5)
                result = sock.connect_ex(('localhost', port))
                sock.close()
                return result != 0
            except:
                return False

        def apply_port():
            try:
                new_port = int(port_var.get())
                if new_port < 1 or new_port > 65535:
                    messagebox.showerror("Ошибка",
                                         "Порт должен быть в диапазоне 1-65535", parent=dialog)
                    return

                if new_port == self.webserver_port:
                    dialog.destroy()
                    return

                if not is_port_available(new_port):
                    # Пробуем подождать, вдруг порт освободится
                    for attempt in range(3):
                        if is_port_available(new_port):
                            break
                        time.sleep(0.5)

                    if not is_port_available(new_port):
                        messagebox.showerror("Порт занят",
                                             f"Порт {new_port} уже занят другим процессом.\nПопробуйте другой порт.",
                                             parent=dialog)
                        return

                old_port = self.webserver_port
                self.webserver_port = new_port

                self.port_btn.config(text=f"Порт: {self.webserver_port}")

                # Пересоздаём веб-сервер с новым портом
                self.webserver = WebServer(
                    self.renderer, port=self.webserver_port)

                self.save_settings()

                self.show_temporary_message(
                    "Порт изменен",
                    f"Порт веб-сервера изменен: {old_port} → {self.webserver_port}"
                )

                dialog.destroy()

            except ValueError:
                messagebox.showerror("Ошибка",
                                     "Порт должен быть целым числом", parent=dialog)
            except Exception as e:
                messagebox.showerror("Ошибка",
                                     f"Не удалось изменить порт: {e}", parent=dialog)
                logger.error(f"Error changing port: {e}")

        btn_frame = ttk.Frame(dialog)
        btn_frame.pack(pady=15)

        ttk.Button(btn_frame, text="Применить",
                   command=apply_port).pack(side="left", padx=5)
        ttk.Button(btn_frame, text="Отмена",
                   command=dialog.destroy).pack(side="left", padx=5)

        dialog.bind("<Return>", lambda e: apply_port())

        self.root.wait_window(dialog)

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
        rounded_value = round(float(value))

        if param == 'amplitude':
            self.wave_amplitude.set(rounded_value)
            self.wave_amplitude_label.config(text=f"{rounded_value:.2f}")
        elif param == 'frequency':
            self.wave_frequency.set(rounded_value)
            self.wave_frequency_label.config(text=f"{rounded_value:.2f}")
        elif param == 'speed':
            self.wave_speed.set(rounded_value)
            self.wave_speed_label.config(text=f"{rounded_value}")

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
        logger.info(
            f"Noise gate {'enabled' if enabled else 'disabled'} with threshold: {threshold}")
        self.save_settings()  # Автоматическое сохранение

    def update_noise_gate_threshold(self):
        """Обновление порога подавления шума"""
        if self.noise_gate_enabled.get():
            threshold = self.noise_gate_threshold.get()
            self.audio.noise_gate_threshold = threshold
            self.renderer.set_noise_gate(threshold)
            logger.info(f"Noise gate threshold updated to: {threshold}")
        self.save_settings()  # Автоматическое сохранение

    def update_wave_params(self):
        """Обновляет параметры эффекта 'Волна'"""
        self.wave_params = {
            'amplitude': self.wave_amplitude.get(),
            'frequency': self.wave_frequency.get(),
            'speed': self.wave_speed.get()  # Теперь целое число 0-5
        }

        # Обновляем эффект в рендерере только если он включен в интерфейсе
        wave_enabled = self.wave_effect.get()
        if wave_enabled:
            self.renderer.set_wave(
                True,
                self.wave_params['amplitude'],
                self.wave_params['frequency'],
                self.wave_params['speed']  # Скорость как целое число
            )
        else:
            # Если волна выключена в интерфейсе, выключаем ее и в рендерере
            self.renderer.set_wave(False, 0, 0, 0)

        self.save_settings()

    def update_effects(self):
        """Обновление эффектов"""
        effects = {
            'shake': self.shake.get(),
            'bounce': self.bounce.get(),
            'pulse': self.pulse.get(),
            'blink': self.blink.get(),
            'random_effect': self.random_effect.get(),
            'wave': self.wave_effect.get()
        }
        self.renderer.set_effects(effects)

        # Обновляем параметры эффекта 'Волна' в зависимости от состояния чекбокса
        self.update_wave_params()

        logger.info(f"Effects updated: {effects}")
        self.save_settings()  # Автоматическое сохранение

    def update_idle_setting(self):
        """Обновление настройки idle-режима с плавным затемнением"""
        enabled = self.idle_enabled.get()
        timeout = self.idle_timeout.get()
        fade = self.idle_fade_duration.get()
        restore = self.idle_restore_duration.get()
        self.renderer.set_idle(enabled, timeout, fade, restore)
        logger.info(
            f"Idle mode updated: enabled={enabled}, timeout={timeout}, fade={fade}, restore={restore}")
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
            'effects': {
                'shake': self.shake.get(),
                'bounce': self.bounce.get(),
                'pulse': self.pulse.get(),
                'blink': self.blink.get(),
                'random_effect': self.random_effect.get(),
                'wave': self.wave_effect.get()
            },
            'wave_params': self.wave_params,
            'sensitivity': self.sensitivity.get(),
            'noise_gate_enabled': self.noise_gate_enabled.get(),
            'noise_gate_threshold': self.noise_gate_threshold.get(),
            'mic_device': self.device_var.get(),
            'idle_enabled': self.idle_enabled.get(),
            'idle_timeout': self.idle_timeout.get(),
            'idle_fade_duration': self.idle_fade_duration.get(),
            'idle_restore_duration': self.idle_restore_duration.get(),
            'current_slot': self.current_slot,
            'webserver_port': self.webserver_port,
            'sections_state': {
                'effects': self.effects_expanded,
                'wave': self.wave_expanded,
                'idle': self.idle_expanded,
                'thresh': self.thresh_expanded,
                'states': self.states_expanded
            },
            'host_api_index': self.host_api_index  # Сохраняем выбранный API
        }
        try:
            with open(SETTINGS_FILE, 'w') as f:
                json.dump(settings, f, indent=2)
            logger.info("Settings saved automatically")
        except Exception as e:
            logger.error(f"Error saving settings: {e}")

    def toggle_section(self, section_name):
        """Сворачивание/разворачивание секций настроек"""
        if section_name == "effects":
            if self.effects_expanded:
                self.effects_content.pack_forget()
                self.effects_expanded = False
                self.effects_header_label.config(text="▶ Глобальные эффекты")
            else:
                self.effects_content.pack(fill="x", padx=3, pady=(0, 2))
                self.effects_expanded = True
                self.effects_header_label.config(text="▼ Глобальные эффекты")
        elif section_name == "wave":
            if self.wave_expanded:
                self.wave_content.pack_forget()
                self.wave_expanded = False
                self.wave_header_label.config(text="▶ Настройки Волны")
            else:
                self.wave_content.pack(fill="x", padx=3, pady=(0, 2))
                self.wave_expanded = True
                self.wave_header_label.config(text="▼ Настройки Волны")
        elif section_name == "idle":
            if self.idle_expanded:
                self.idle_content.pack_forget()
                self.idle_expanded = False
                self.idle_header_label.config(text="▶ Idle-режим")
            else:
                self.idle_content.pack(fill="x", padx=3, pady=(0, 2))
                self.idle_expanded = True
                self.idle_header_label.config(text="▼ Idle-режим")
        elif section_name == "thresh":
            if self.thresh_expanded:
                self.thresh_content.pack_forget()
                self.thresh_expanded = False
                self.thresh_header_label.config(text="▶ Пороги голоса")
            else:
                self.thresh_content.pack(fill="x", padx=3, pady=(0, 2))
                self.thresh_expanded = True
                self.thresh_header_label.config(text="▼ Пороги голоса")
                self.refresh_thresholds_ui()
        elif section_name == "states":
            if self.states_expanded:
                self.states_content.pack_forget()
                self.states_expanded = False
                self.states_header_label.config(text="▶ Активные состояния")
            else:
                self.states_content.pack(fill="x", padx=3, pady=(0, 2))
                self.states_expanded = True
                self.states_header_label.config(text="▼ Активные состояния")
                self.refresh_states_ui()

        self.save_settings()

    def refresh_thresholds_ui(self):
        """Обновляет UI порогов на основе текущих состояний рта из модели"""
        # Очищаем предыдущие виджеты
        for widget in self.thresh_content.winfo_children():
            widget.destroy()

        self.thresh_vars = []
        self.threshold_lines = {}  # Будет заполнено в update_threshold_visuals
        self.threshold_labels = {}  # Для хранения ссылок на метки порогов

        if not hasattr(self.renderer, 'model') or not self.renderer.model:
            ttk.Label(self.thresh_content,
                      text="(Загрузите модель)").pack(pady=5)
            return

        mouth_states = self.renderer.model.get('mouth_states', [])
        if not mouth_states:
            ttk.Label(self.thresh_content,
                      text="(Нет состояний рта)").pack(pady=5)
            return

        # Создаем сетку для порогов
        thresholds_grid = ttk.Frame(self.thresh_content)
        thresholds_grid.pack(fill="x", padx=2, pady=2)

        row = 0
        col = 0
        for i, state in enumerate(mouth_states):
            state_name = state.get('name', f'Состояние {i+1}')
            # Создаем переменную для этого состояния
            var = tk.DoubleVar(value=state.get('threshold', 0.0))
            self.thresh_vars.append(var)

            # Метка с названием
            ttk.Label(thresholds_grid, text=f"{state_name}:").grid(
                row=row, column=col*2, sticky="w", padx=1, pady=1)
            # Поле ввода
            entry = ttk.Entry(thresholds_grid, textvariable=var, width=6)
            entry.grid(row=row, column=col*2+1, padx=1, pady=1)
            entry.bind("<Return>", lambda e,
                       idx=i: self.update_single_threshold(idx))
            entry.bind("<FocusOut>", lambda e,
                       idx=i: self.update_single_threshold(idx))

            # Переходим к следующей колонке/строке
            col += 1
            if col >= 2:  # Две колонки
                col = 0
                row += 1

        # Подсказка
        help_label = ttk.Label(
            thresholds_grid,
            text="Значения: 0.0-1.0",
            font=("Arial", 7)
        )
        help_label.grid(row=row+1, column=0, columnspan=4, pady=(0, 1))

        # Обновляем визуализацию порогов
        self.update_threshold_visuals()

    def refresh_states_ui(self):
        """Обновляет UI активных состояний на основе текущей модели"""
        # Очищаем предыдущие виджеты
        for widget in self.states_content.winfo_children():
            widget.destroy()

        self.state_vars = []  # Список булевых переменных для активных состояний

        if not hasattr(self.renderer, 'model') or not self.renderer.model:
            ttk.Label(self.states_content,
                      text="(Загрузите модель)").pack(pady=5)
            return

        mouth_states = self.renderer.model.get('mouth_states', [])
        if not mouth_states:
            ttk.Label(self.states_content,
                      text="(Нет состояний рта)").pack(pady=5)
            return

        states_grid = ttk.Frame(self.states_content)
        states_grid.pack(fill="x", padx=2, pady=2)

        row = 0
        col = 0
        for i, state in enumerate(mouth_states):
            state_name = state.get('name', f'Состояние {i+1}')
            var = tk.BooleanVar(value=state.get('active', True))
            self.state_vars.append(var)

            cb = ttk.Checkbutton(states_grid, text=state_name, variable=var,
                                 command=lambda idx=i: self.update_single_active_state(idx))
            cb.grid(row=row, column=col, sticky="w", padx=3, pady=1)

            col += 1
            if col >= 2:  # Две колонки
                col = 0
                row += 1

    def update_single_threshold(self, state_index):
        """Обновляет порог для одного состояния и сохраняет в модель"""
        if not hasattr(self.renderer, 'model') or not self.renderer.model:
            return

        mouth_states = self.renderer.model.get('mouth_states', [])
        if state_index < 0 or state_index >= len(mouth_states):
            return

        try:
            new_threshold = self.thresh_vars[state_index].get()
            new_threshold = max(0.0, min(1.0, new_threshold))
            mouth_states[state_index]['threshold'] = new_threshold

            # Обновляем пороги в рендерере
            self.renderer.set_mouth_states(mouth_states)

            # Обновляем визуализацию
            self.update_threshold_visuals()

            # Сохраняем модель в слот
            if self.current_slot:
                self.save_model_to_current_slot()

        except Exception as e:
            logger.error(f"Error updating single threshold: {e}")

    def update_single_active_state(self, state_index):
        """Обновляет активность одного состояния и сохраняет в модель"""
        if not hasattr(self.renderer, 'model') or not self.renderer.model:
            return

        mouth_states = self.renderer.model.get('mouth_states', [])
        if state_index < 0 or state_index >= len(mouth_states):
            return

        try:
            mouth_states[state_index]['active'] = self.state_vars[state_index].get()

            # Обновляем активные состояния в рендерере
            self.renderer.set_mouth_states(mouth_states)

            # Обновляем визуализацию порогов (линии неактивных скроются)
            self.update_threshold_visuals()

            # Сохраняем модель в слот
            if self.current_slot:
                self.save_model_to_current_slot()

        except Exception as e:
            logger.error(f"Error updating single active state: {e}")

    def save_model_to_current_slot(self):
        """Сохраняет текущую модель в текущий слот"""
        if not self.current_slot or not self.renderer.model:
            return

        try:
            slot_dir = os.path.join(MODELS_DIR, f"slot{self.current_slot}")
            os.makedirs(slot_dir, exist_ok=True)

            json_path = os.path.join(slot_dir, "model.json")
            with open(json_path, "w", encoding="utf-8") as f:
                json.dump(self.renderer.model, f, indent=2, ensure_ascii=False)

            # Обновляем кнопку слота
            self._update_single_slot(self.current_slot - 1)

            logger.info(f"Model auto-saved to slot {self.current_slot}")

        except Exception as e:
            logger.error(f"Error auto-saving model to slot: {e}")

    def update_threshold_visuals(self):
        """Обновление визуализации порогов - только активные состояния"""
        canvas_width = self.level_canvas.winfo_width()
        if canvas_width < 10:
            return

        self.level_canvas.delete("threshold_line")
        self.level_canvas.delete("threshold_label")

        if not hasattr(self.renderer, 'model') or not self.renderer.model:
            return

        mouth_states = self.renderer.model.get('mouth_states', [])
        if not mouth_states:
            return

        self.threshold_lines = {}

        for i, state in enumerate(mouth_states):
            if not state.get('active', True):
                continue

            try:
                val = float(state.get('threshold', 0.0))
            except Exception:
                val = 0.0

            pos = min(1.0, max(0.0, val)) * canvas_width

            line_id = self.level_canvas.create_line(
                pos, 0, pos, 25,
                dash=(2, 2), width=1, fill="#2196F3",
                tags="threshold_line"
            )
            self.threshold_lines[f"state_{i}"] = line_id

            if i == 0:
                anchor = "w"
            elif i == len(mouth_states) - 1:
                anchor = "e"
            else:
                anchor = "center"

            state_name = state.get('name', f'S{i+1}')[:6]
            self.level_canvas.create_text(
                pos, 7,
                text=state_name,
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
            if not hasattr(self, 'level_indicator'):
                self.level_indicator = self.level_canvas.create_rectangle(
                    0, 0, 0, 25, outline="", fill="#4CAF50", tags="level_bar")
            self.level_canvas.coords(
                self.level_indicator, 0, 0, indicator_width, 25)
        except Exception as e:
            logger.error(f"Error updating level indicator: {e}")

        if hasattr(self.renderer, 'model') and self.renderer.model:
            mouth_states = self.renderer.model.get('mouth_states', [])
            if mouth_states:
                current_state_idx = -1
                for i, state in enumerate(mouth_states):
                    if state.get('active', True) and level_clamped >= state.get('threshold', 0.0):
                        current_state_idx = i

                colors = ["#888888", "#2196F3",
                          "#4CAF50", "#FFC107", "#f44336"]
                if 0 <= current_state_idx < len(colors):
                    color = colors[current_state_idx]
                else:
                    color = "#4CAF50"
            else:
                color = "#4CAF50"
        else:
            color = "#4CAF50"

        try:
            self.level_canvas.itemconfig(self.level_indicator, fill=color)
        except Exception as e:
            logger.error(f"Error setting level indicator color: {e}")

    def on_canvas_resize(self, event=None):
        """Обработка изменения размера канваса"""
        self.update_threshold_visuals()
        self.update_level_indicator(self.audio_level_scaled if hasattr(
            self, 'audio_level_scaled') else 0)

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

            self.renderer.model = {
                "name": f"Слот {idx+1}",
                "layers": [],
                "groups": [],
                "mouth_states": [
                    {'id': 0, 'name': 'Тишина', 'threshold': 0.0, 'active': True},
                    {'id': 1, 'name': 'Шёпот', 'threshold': 0.3, 'active': True},
                    {'id': 2, 'name': 'Норма', 'threshold': 0.6, 'active': True},
                    {'id': 3, 'name': 'Крик', 'threshold': 0.9, 'active': True}
                ]
            }
            self.renderer.model_dir = slot_dir
            os.makedirs(slot_dir, exist_ok=True)

            with open(json_path, "w", encoding="utf-8") as f:
                json.dump(self.renderer.model, f, indent=2, ensure_ascii=False)
        else:
            with open(json_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if 'mouth_states' not in data or not data['mouth_states']:
                data['mouth_states'] = [
                    {'id': 0, 'name': 'Тишина', 'threshold': 0.0, 'active': True},
                    {'id': 1, 'name': 'Шёпот', 'threshold': 0.3, 'active': True},
                    {'id': 2, 'name': 'Норма', 'threshold': 0.6, 'active': True},
                    {'id': 3, 'name': 'Крик', 'threshold': 0.9, 'active': True}
                ]
            self.renderer.load_model(data, slot_dir)
            if self.webserver:
                self.webserver.renderer = self.renderer

        self.current_slot = idx + 1

        self.refresh_thresholds_ui()
        self.refresh_states_ui()

        self.refresh_slot_buttons()

        self.save_settings()

        model_name = self.renderer.model.get('name', 'модель')

        if not silent:
            logger.info(f"Model loaded from slot {idx+1}: {model_name}")
            self.show_temporary_message(
                "Загружено", f"Модель загружена из слота {idx+1}")

    def open_editor(self):
        """Открытие редактора моделей"""
        try:
            main_window = self.root
            main_window.app = self
            main_window.attributes('-disabled', True)

            editor = ModelEditor(
                main_window,
                on_save=self.on_model_saved,
                device=self.device_var.get(),
                noise_gate_threshold=self.noise_gate_threshold.get(
                ) if self.noise_gate_enabled.get() else 0.0,
                sensitivity=self.sensitivity.get(),
                current_slot=self.current_slot,
                renderer=self.renderer
            )

            def on_editor_close():
                try:
                    editor.audio_processor.stop()
                except Exception as e:
                    logger.error(f"Error stopping editor audio: {e}")

                main_window.attributes('-disabled', False)
                main_window.focus_set()

                self.refresh_slot_buttons()
                self.refresh_thresholds_ui()
                self.refresh_states_ui()

                try:
                    self.audio.stop()
                    self.audio = AudioProcessor(
                        callback=self.on_audio_level,
                        device=self.device_var.get(),
                        host_api_index=self.host_api_index  # передаём текущий API
                    )
                    self.audio.noise_gate_threshold = self.noise_gate_threshold.get(
                    ) if self.noise_gate_enabled.get() else 0.0
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
            messagebox.showerror(
                "Ошибка редактора", f"Не удалось открыть редактор: {e}. Смотри error.log")
            logger.error(f"Error opening editor: {e}\n{tb}")
            self.root.attributes('-disabled', False)

    def on_model_saved(self, model_data, model_dir, slot_num=None):
        """Обработка сохранения модели"""
        self.refresh_slot_buttons()
        logger.info(f"Model saved to directory: {model_dir}")
        if slot_num == self.current_slot:
            logger.info(f"Model saved to current slot {slot_num}, reloading")
            self.load_slot(slot_num - 1, silent=True)

    def toggle_server(self):
        """Переключение веб-сервера"""
        if self.webserver and getattr(self.webserver, "is_running", False):
            self.webserver.stop()
            self.server_btn.config(text="🌐 Запустить веб-сервер")
            self.link_btn.config(state="disabled")
            self.port_btn.config(state="normal")
            logger.info("Web server stopped")

            if self.renderer_was_started:
                try:
                    self.renderer.stop()
                    self.renderer_was_started = False
                    logger.info("Renderer stopped")
                except Exception as e:
                    logger.error(f"Error stopping renderer: {e}")
        else:
            if not self.webserver:
                self.webserver = WebServer(
                    self.renderer, port=self.webserver_port)
            elif not self.webserver.is_running:
                self.webserver.renderer = self.renderer

            if not self.renderer_was_started:
                try:
                    self.renderer.set_noise_gate(
                        self.noise_gate_threshold.get() if self.noise_gate_enabled.get() else 0.0)
                    self.renderer.set_idle(
                        self.idle_enabled.get(),
                        self.idle_timeout.get(),
                        self.idle_fade_duration.get(),
                        self.idle_restore_duration.get()
                    )
                    self.renderer.set_effects(self.effects)

                    if self.wave_effect.get():
                        self.renderer.set_wave(
                            True,
                            self.wave_params['amplitude'],
                            self.wave_params['frequency'],
                            self.wave_params['speed']
                        )
                    else:
                        self.renderer.set_wave(False, 0, 0, 0)

                    if self.renderer.model and 'mouth_states' in self.renderer.model:
                        self.renderer.set_mouth_states(
                            self.renderer.model['mouth_states'])

                    self.renderer.start()
                    self.renderer_was_started = True
                    logger.info("Renderer started for web server")
                except Exception as e:
                    logger.error(f"Error starting renderer: {e}")

            try:
                self.webserver.start()
                self.server_btn.config(text="⏹️ Остановить веб-сервер")
                self.link_btn.config(state="normal")
                self.port_btn.config(state="disabled")
                logger.info("Web server started")
            except Exception as e:
                logger.error(f"Error starting web server: {e}")
                messagebox.showerror(
                    "Ошибка", f"Не удалось запустить веб-сервер: {e}")

    def on_close(self):
        """Обработка закрытия приложения"""
        try:
            self.audio.stop()
        except Exception as e:
            logger.error(f"Error stopping audio: {e}")

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

        self.link_btn.config(state="disabled")

        self.save_settings()
        logger.info("Application closed")
        self.root.destroy()


if __name__ == "__main__":
    root = tk.Tk()
    app = App(root)
    root.mainloop()
