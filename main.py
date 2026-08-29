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
from locale_loader import tr, i18n

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
        root.title(tr('app_title'))
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

        # Установка размера окна: компактное
        root.geometry("800x500")
        root.minsize(800, 500)
        logger.info("Application started")

        # Флаг инициализации для предотвращения сохранения при начальной загрузке
        self.initializing = True

        # Оптимизации Tkinter
        root.update_idletasks()
        root.option_add('*tearOff', False)

        # Оптимизация частоты обновления UI
        self._ui_update_interval = 50  # 20 FPS для UI
        self._last_ui_update = 0

        # Загрузка настроек
        self.settings = self.load_settings()

        # Устанавливаем язык из настроек
        lang = self.settings.get('language', 'ru')
        if lang in i18n.get_available_languages():
            i18n.set_lang(lang)

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

        # Регистрируем callback для обновления UI при смене языка
        i18n.register_callback(self.refresh_ui_texts)

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
        models_frame = ttk.LabelFrame(main_frame, text=tr('models_frame'))
        models_frame.grid(row=0, column=0, sticky="nsew", padx=(0, 3), pady=0)
        self._register_widget_i18n(models_frame, 'models_frame')

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
                photo = self.load_preview_for_slot(idx)
                btn_frame = ttk.Frame(slots_grid)
                btn_frame.grid(row=r, column=c, padx=1, pady=1, sticky="nsew")

                btn = ttk.Button(btn_frame, text=f"{tr('slot')} {idx+1}",
                                 image=photo, compound="top",
                                 command=lambda i=idx: self.load_slot(i))
                btn.pack(fill="both", expand=True, padx=0, pady=0)
                self._register_widget_i18n(btn, 'slot', suffix=f" {idx+1}")  # сложный текст – обновим отдельно

                if photo:
                    btn.photo = photo
                self.model_slots.append(btn)

        slots_grid.columnconfigure(0, weight=1)
        slots_grid.columnconfigure(1, weight=1)
        for r in range(3):
            slots_grid.rowconfigure(r, weight=1)

        # ---- КОЛОНКА 2: Основные настройки (микрофон и управление) ----
        settings_frame = ttk.LabelFrame(main_frame, text=tr('settings_frame'))
        settings_frame.grid(row=0, column=1, sticky="nsew", padx=3, pady=0)
        self._register_widget_i18n(settings_frame, 'settings_frame')

        # Управление
        control_frame = ttk.LabelFrame(settings_frame, text=tr('settings_frame'))
        control_frame.pack(fill="x", pady=(0, 3), padx=3)
        self._register_widget_i18n(control_frame, 'settings_frame')

        self.editor_btn = ttk.Button(control_frame, text=tr('editor_btn'),
                                     command=self.open_editor)
        self.editor_btn.pack(fill="x", padx=2, pady=2)
        self._register_widget_i18n(self.editor_btn, 'editor_btn')

        self.server_btn = ttk.Button(control_frame, text=tr('server_btn'),
                                     command=self.toggle_server)
        self.server_btn.pack(fill="x", padx=2, pady=2)
        self._register_widget_i18n(self.server_btn, 'server_btn')

        self.link_btn = ttk.Button(
            control_frame,
            text=tr('link_btn'),
            command=self.open_web_link,
            state="disabled"
        )
        self.link_btn.pack(fill="x", padx=2, pady=2)
        self._register_widget_i18n(self.link_btn, 'link_btn')

        self.port_btn = ttk.Button(
            control_frame,
            text=tr('port_btn', port=self.webserver_port),
            command=self.change_port
        )
        self.port_btn.pack(fill="x", padx=2, pady=2)
        self._register_widget_i18n(self.port_btn, 'port_btn', port=self.webserver_port)

        # ---- Выбор языка ----
        lang_frame = ttk.Frame(settings_frame)
        lang_frame.pack(fill="x", pady=2, padx=3)
        lang_label = ttk.Label(lang_frame, text="Язык / Language:")
        lang_label.pack(side="left", padx=2)

        available_codes = i18n.get_available_languages()
        available_display = [i18n.get_language_display_name(code) for code in available_codes]
        self.lang_var = tk.StringVar(value=i18n.get_language_display_name(i18n.lang))
        lang_combo = ttk.Combobox(lang_frame, textvariable=self.lang_var, state="readonly", width=12)
        lang_combo['values'] = available_display
        if self.lang_var.get() in available_display:
            lang_combo.current(available_display.index(self.lang_var.get()))
        else:
            lang_combo.current(0)
        lang_combo.pack(side="left", padx=2)
        lang_combo.bind('<<ComboboxSelected>>', self.on_lang_change)
        # Отключаем прокрутку колесиком для комбобокса
        lang_combo.bind('<MouseWheel>', lambda e: 'break')

        # Настройки микрофона
        mic_frame = ttk.LabelFrame(settings_frame, text=tr('mic_frame'))
        mic_frame.pack(fill="x", pady=(0, 3), padx=3)
        self._register_widget_i18n(mic_frame, 'mic_frame')

        # === НОВОЕ: выбор Host API ===
        audio_api_label = ttk.Label(mic_frame, text=tr('audio_api'))
        audio_api_label.pack(anchor='w', padx=2, pady=(2, 0))
        self._register_widget_i18n(audio_api_label, 'audio_api')

        api_row = ttk.Frame(mic_frame)
        api_row.pack(fill='x', padx=2, pady=(0, 2))
        self.host_api_combo = ttk.Combobox(api_row, state="readonly", width=20)
        self.host_api_combo.pack(side='left', fill='x', expand=True)
        self.host_api_combo.bind('<<ComboboxSelected>>', self.on_host_api_change)
        self.host_api_combo.bind('<MouseWheel>', lambda e: 'break')

        # Выбор устройства
        device_label = ttk.Label(mic_frame, text=tr('device'))
        device_label.pack(anchor='w', padx=2, pady=(2, 0))
        self._register_widget_i18n(device_label, 'device')

        device_row = ttk.Frame(mic_frame)
        device_row.pack(fill='x', padx=2, pady=(0, 2))

        self.device_var = tk.StringVar(
            value=self.settings.get('mic_device', ''))
        self.device_combo = ttk.Combobox(
            device_row, textvariable=self.device_var, width=18)
        self.device_combo.pack(side='left', fill='x', expand=True)
        self.device_combo.bind('<<ComboboxSelected>>', self.on_device_change)
        self.device_combo.bind('<MouseWheel>', lambda e: 'break')

        refresh_btn = ttk.Button(device_row, text="↻",
                                 width=3, command=self.refresh_devices)
        refresh_btn.pack(side='left', padx=(2, 0))

        self.device_combo.bind('<<ComboboxSelected>>', self.on_device_change)

        # Заполняем список Host API и устройств
        self.refresh_host_apis()
        self.refresh_devices()

        level_label = ttk.Label(mic_frame, text=tr('level'))
        level_label.pack(anchor="w", padx=2, pady=(2, 0))
        self._register_widget_i18n(level_label, 'level')

        self.vol_label = ttk.Label(mic_frame, text="0.00")
        self.vol_label.pack(anchor="w", padx=2, pady=(0, 2))

        # Чувствительность с шагом 5%
        sens_frame = ttk.Frame(mic_frame)
        sens_frame.pack(fill="x", padx=2, pady=0)

        sens_label = ttk.Label(sens_frame, text=tr('sensitivity'))
        sens_label.pack(anchor="w", side="left")
        self._register_widget_i18n(sens_label, 'sensitivity')

        self.sensitivity = tk.DoubleVar(value=self._round_to_step(
            self.settings.get('sensitivity', 1.5), 0.05))
        self.sens_percent_label = ttk.Label(
            sens_frame, text=f"{self.sensitivity.get()*100:.0f}%")
        self.sens_percent_label.pack(anchor="e", side="right")

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
        noise_gate_cb = ttk.Checkbutton(noise_gate_frame, text=tr('noise_gate'), variable=self.noise_gate_enabled,
                                        command=self.toggle_noise_gate)
        noise_gate_cb.pack(side="left")
        self._register_widget_i18n(noise_gate_cb, 'noise_gate')

        self.noise_gate_value_label = ttk.Label(noise_gate_frame, text="0.010")
        self.noise_gate_value_label.pack(side="right", padx=2)

        self.noise_gate_threshold = tk.DoubleVar(value=self._round_to_step(
            self.settings.get('noise_gate_threshold', 0.01), 0.005))

        noise_gate_scale = ttk.Scale(mic_frame, from_=0.001, to=0.05, variable=self.noise_gate_threshold,
                                     orient="horizontal", length=180)
        noise_gate_scale.pack(fill="x", padx=2, pady=1)
        noise_gate_scale.configure(command=self._on_noise_gate_scale_move)
        noise_gate_scale.bind("<ButtonRelease-1>",
                              lambda e: self.update_noise_gate_threshold())

        # Индикатор уровня
        indicator_label = ttk.Label(mic_frame, text=tr('indicator'))
        indicator_label.pack(anchor="w", padx=2, pady=(3, 0))
        self._register_widget_i18n(indicator_label, 'indicator')

        self.level_canvas = tk.Canvas(
            mic_frame, width=180, height=25, bg="#f0f0f0")
        self.level_canvas.bind("<Configure>", self.on_canvas_resize)
        self.level_canvas.pack(fill="x", padx=2, pady=(0, 3))

        # ---- КОЛОНКА 3: Расширенные настройки ----
        expandable_frame = ttk.LabelFrame(
            main_frame, text=tr('advanced_frame'))
        expandable_frame.grid(
            row=0, column=2, sticky="nsew", padx=(3, 0), pady=0)
        self._register_widget_i18n(expandable_frame, 'advanced_frame')

        self.expand_canvas = tk.Canvas(expandable_frame, bg="#f0f0f0")
        expand_scrollbar = ttk.Scrollbar(
            expandable_frame, orient="vertical", command=self.expand_canvas.yview)
        self.expand_canvas.configure(yscrollcommand=expand_scrollbar.set)

        expand_scrollbar.pack(side="right", fill="y")
        self.expand_canvas.pack(side="left", fill="both", expand=True)

        self.expand_content = ttk.Frame(self.expand_canvas)
        self.expand_canvas.create_window(
            (0, 0), window=self.expand_content, anchor="nw", width=350)

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
        effects_text = "▼ " + tr('effects_frame') if self.effects_expanded else "▶ " + tr('effects_frame')
        self.effects_header_label = ttk.Label(
            effects_header, text=effects_text, font=("Arial", 9, "bold"), cursor="hand2")
        self.effects_header_label.pack(side="left", padx=2, pady=2)
        self._register_widget_i18n(self.effects_header_label, 'effects_frame', prefix="▼ " if self.effects_expanded else "▶ ")
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
        shake_cb = ttk.Checkbutton(effects_grid, text=tr('shake'), variable=self.shake,
                                   command=self.update_effects)
        shake_cb.pack(anchor="w", padx=3, pady=1)
        self._register_widget_i18n(shake_cb, 'shake')

        self.bounce = tk.BooleanVar(value=self.effects.get('bounce', False))
        bounce_cb = ttk.Checkbutton(effects_grid, text=tr('bounce'), variable=self.bounce,
                                    command=self.update_effects)
        bounce_cb.pack(anchor="w", padx=3, pady=1)
        self._register_widget_i18n(bounce_cb, 'bounce')

        self.pulse = tk.BooleanVar(value=self.effects.get('pulse', False))
        pulse_cb = ttk.Checkbutton(effects_grid, text=tr('pulse'), variable=self.pulse,
                                   command=self.update_effects)
        pulse_cb.pack(anchor="w", padx=3, pady=1)
        self._register_widget_i18n(pulse_cb, 'pulse')

        self.blink = tk.BooleanVar(value=self.effects.get('blink', True))
        blink_cb = ttk.Checkbutton(effects_grid, text=tr('blink'), variable=self.blink,
                                   command=self.update_effects)
        blink_cb.pack(anchor="w", padx=3, pady=1)
        self._register_widget_i18n(blink_cb, 'blink')

        self.random_effect = tk.BooleanVar(
            value=self.effects.get('random_effect', True))
        random_cb = ttk.Checkbutton(effects_grid, text=tr('random_effect'), variable=self.random_effect,
                                    command=self.update_effects)
        random_cb.pack(anchor="w", padx=3, pady=1)
        self._register_widget_i18n(random_cb, 'random_effect')

        self.wave_effect = tk.BooleanVar(value=self.effects.get('wave', False))
        wave_cb = ttk.Checkbutton(effects_grid, text=tr('wave'), variable=self.wave_effect,
                                  command=self.update_effects)
        wave_cb.pack(anchor="w", padx=3, pady=1)
        self._register_widget_i18n(wave_cb, 'wave')

        # ---- Настройки эффекта "Волна" (второе место) ----
        wave_frame = ttk.Frame(self.expand_content)
        wave_frame.pack(fill="x", pady=(0, 3))
        wave_header = ttk.Frame(wave_frame)
        wave_header.pack(fill="x")

        self.wave_expanded = self.sections_state.get('wave', True)
        wave_text = "▼ " + tr('wave_settings') if self.wave_expanded else "▶ " + tr('wave_settings')
        self.wave_header_label = ttk.Label(
            wave_header, text=wave_text, font=("Arial", 9, "bold"), cursor="hand2")
        self.wave_header_label.pack(side="left", padx=2, pady=2)
        self._register_widget_i18n(self.wave_header_label, 'wave_settings', prefix="▼ " if self.wave_expanded else "▶ ")
        wave_header.bind("<Button-1>", lambda e: self.toggle_section("wave"))
        self.wave_header_label.bind(
            "<Button-1>", lambda e: self.toggle_section("wave"))
        self.wave_content = ttk.Frame(wave_frame)

        if self.wave_expanded:
            self.wave_content.pack(fill="x", padx=3, pady=(0, 2))

        wave_settings_grid = ttk.Frame(self.wave_content)
        wave_settings_grid.pack(fill="x", padx=2, pady=2)

        amplitude_frame = ttk.Frame(wave_settings_grid)
        amplitude_frame.pack(fill="x", padx=3, pady=(2, 0))
        amp_label = ttk.Label(amplitude_frame, text=tr('wave_amplitude'))
        amp_label.pack(anchor="w", side="left")
        self._register_widget_i18n(amp_label, 'wave_amplitude')

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

        frequency_frame = ttk.Frame(wave_settings_grid)
        frequency_frame.pack(fill="x", padx=3, pady=(2, 0))
        freq_label = ttk.Label(frequency_frame, text=tr('wave_frequency'))
        freq_label.pack(anchor="w", side="left")
        self._register_widget_i18n(freq_label, 'wave_frequency')

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

        speed_frame = ttk.Frame(wave_settings_grid)
        speed_frame.pack(fill="x", padx=3, pady=(2, 0))
        speed_label = ttk.Label(speed_frame, text=tr('wave_speed'))
        speed_label.pack(anchor="w", side="left")
        self._register_widget_i18n(speed_label, 'wave_speed')

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
        idle_text = "▼ " + tr('idle_mode') if self.idle_expanded else "▶ " + tr('idle_mode')
        self.idle_header_label = ttk.Label(
            idle_header, text=idle_text, font=("Arial", 9, "bold"), cursor="hand2")
        self.idle_header_label.pack(side="left", padx=2, pady=2)
        self._register_widget_i18n(self.idle_header_label, 'idle_mode', prefix="▼ " if self.idle_expanded else "▶ ")
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
        idle_cb = ttk.Checkbutton(idle_grid, text=tr('idle_enable'), variable=self.idle_enabled,
                                  command=self.update_idle_setting)
        idle_cb.pack(anchor="w", padx=3, pady=1)
        self._register_widget_i18n(idle_cb, 'idle_enable')

        timeout_label = ttk.Label(idle_grid, text=tr('idle_timeout'))
        timeout_label.pack(anchor="w", padx=3, pady=(3, 0))
        self._register_widget_i18n(timeout_label, 'idle_timeout')

        self.idle_timeout = tk.DoubleVar(
            value=self.settings.get('idle_timeout', 5.0))
        idle_entry = ttk.Entry(
            idle_grid, textvariable=self.idle_timeout, width=10)
        idle_entry.pack(anchor="w", padx=3, pady=(0, 2))
        idle_entry.bind("<Return>", lambda e: self.update_idle_setting())
        idle_entry.bind("<FocusOut>", lambda e: self.update_idle_setting())

        fade_label = ttk.Label(idle_grid, text=tr('idle_fade'))
        fade_label.pack(anchor="w", padx=3, pady=(3, 0))
        self._register_widget_i18n(fade_label, 'idle_fade')

        self.idle_fade_duration = tk.DoubleVar(
            value=self.settings.get('idle_fade_duration', 0.3))
        fade_entry = ttk.Entry(
            idle_grid, textvariable=self.idle_fade_duration, width=10)
        fade_entry.pack(anchor="w", padx=3, pady=(0, 2))
        fade_entry.bind("<Return>", lambda e: self.update_idle_setting())
        fade_entry.bind("<FocusOut>", lambda e: self.update_idle_setting())

        restore_label = ttk.Label(idle_grid, text=tr('idle_restore'))
        restore_label.pack(anchor="w", padx=3, pady=(3, 0))
        self._register_widget_i18n(restore_label, 'idle_restore')

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
        thresh_text = "▼ " + tr('thresholds') if self.thresh_expanded else "▶ " + tr('thresholds')
        self.thresh_header_label = ttk.Label(
            thresh_header, text=thresh_text, font=("Arial", 9, "bold"), cursor="hand2")
        self.thresh_header_label.pack(side="left", padx=2, pady=2)
        self._register_widget_i18n(self.thresh_header_label, 'thresholds', prefix="▼ " if self.thresh_expanded else "▶ ")
        thresh_header.bind(
            "<Button-1>", lambda e: self.toggle_section("thresh"))
        self.thresh_header_label.bind(
            "<Button-1>", lambda e: self.toggle_section("thresh"))
        self.thresh_content = ttk.Frame(thresh_frame)

        if self.thresh_expanded:
            self.thresh_content.pack(fill="x", padx=3, pady=(0, 2))

        # ---- Активные состояния (пятое место, в самом низу) ----
        states_frame = ttk.Frame(self.expand_content)
        states_frame.pack(fill="x", pady=(0, 3))
        states_header = ttk.Frame(states_frame)
        states_header.pack(fill="x")

        self.states_expanded = self.sections_state.get('states', True)
        states_text = "▼ " + tr('active_states') if self.states_expanded else "▶ " + tr('active_states')
        self.states_header_label = ttk.Label(
            states_header, text=states_text, font=("Arial", 9, "bold"), cursor="hand2")
        self.states_header_label.pack(side="left", padx=2, pady=2)
        self._register_widget_i18n(self.states_header_label, 'active_states', prefix="▼ " if self.states_expanded else "▶ ")
        states_header.bind(
            "<Button-1>", lambda e: self.toggle_section("states"))
        self.states_header_label.bind(
            "<Button-1>", lambda e: self.toggle_section("states"))
        self.states_content = ttk.Frame(states_frame)

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
            self.load_slot(self.current_slot - 1, silent=True)

        # Завершаем инициализацию
        self.initializing = False

        self.root.after(100, self.on_canvas_resize)

        # Запускаем монитор устройств для автоматического обнаружения подключения/отключения микрофона
        self.start_device_monitor()

    # === МЕТОДЫ ДЛЯ ДИНАМИЧЕСКОЙ СМЕНЫ ЯЗЫКА ===

    def _register_widget_i18n(self, widget, key, **kwargs):
        """Сохраняет ключ перевода и дополнительные параметры для виджета."""
        widget._i18n_key = key
        widget._i18n_kwargs = kwargs

    def refresh_ui_texts(self):
        """Обновляет все тексты в интерфейсе согласно текущему языку."""
        def update_widgets(widget):
            # Обновляем текст, если есть ключ
            if hasattr(widget, '_i18n_key'):
                try:
                    kwargs = getattr(widget, '_i18n_kwargs', {})
                    new_text = tr(widget._i18n_key, **kwargs)
                    try:
                        widget.config(text=new_text)
                    except:
                        pass
                except:
                    pass
            # Рекурсивно обходим дочерние элементы
            try:
                for child in widget.winfo_children():
                    update_widgets(child)
            except:
                pass

        # Обновляем все виджеты в главном окне
        update_widgets(self.root)
        # Обновляем заголовок окна
        self.root.title(tr('app_title'))
        # Обновляем кнопку порта с параметром
        self.port_btn.config(text=tr('port_btn', port=self.webserver_port))
        # Обновляем текст на кнопках слотов (они имеют сложный текст)
        for idx, btn in enumerate(self.model_slots):
            slot_dir = os.path.join(MODELS_DIR, f"slot{idx+1}")
            json_path = os.path.join(slot_dir, "model.json")
            is_current = (idx + 1 == self.current_slot)
            prefix = "★ " if is_current else " "
            if os.path.exists(json_path):
                try:
                    with open(json_path, "r", encoding="utf-8") as f:
                        model_data = json.load(f)
                    model_name = model_data.get('name', f"{tr('slot')} {idx+1}")
                    btn.config(text=f"{prefix}{tr('slot')} {idx+1}\n{model_name[:15]}")
                except:
                    btn.config(text=f"{prefix}{tr('slot')} {idx+1}\n{tr('error')}")
            else:
                btn.config(text=f"{prefix}{tr('slot')} {idx+1}\n{tr('empty_slot')}")

        # Обновляем заголовки сворачиваемых секций
        self._update_section_header('effects', self.effects_expanded)
        self._update_section_header('wave', self.wave_expanded)
        self._update_section_header('idle', self.idle_expanded)
        self._update_section_header('thresh', self.thresh_expanded)
        self._update_section_header('states', self.states_expanded)

        # Обновляем язык в комбобоксе (отображаемое название)
        self.lang_var.set(i18n.get_language_display_name(i18n.lang))

        # Если редактор открыт – обновим и его
        if hasattr(self, 'editor') and self.editor and self.editor.winfo_exists():
            self.editor.refresh_ui_texts()

        # Принудительно обновляем текст чекбоксов, которые не были обновлены через _register_widget_i18n
        # (они уже зарегистрированы, но на всякий случай)
        # В данном случае все они зарегистрированы через _register_widget_i18n

    def _update_section_header(self, section, expanded):
        """Обновляет текст заголовка секции."""
        key_map = {
            'effects': 'effects_frame',
            'wave': 'wave_settings',
            'idle': 'idle_mode',
            'thresh': 'thresholds',
            'states': 'active_states'
        }
        if section in key_map:
            label = getattr(self, f"{section}_header_label", None)
            if label:
                prefix = "▼ " if expanded else "▶ "
                label.config(text=prefix + tr(key_map[section]))

    # === ОБРАБОТЧИК СМЕНЫ ЯЗЫКА ===
    def on_lang_change(self, event=None):
        """Обработчик смены языка (без перезапуска)."""
        new_display = self.lang_var.get()
        available_codes = i18n.get_available_languages()
        new_lang = None
        for code in available_codes:
            if i18n.get_language_display_name(code) == new_display:
                new_lang = code
                break
        if new_lang and new_lang != i18n.lang:
            i18n.set_lang(new_lang)          # меняем язык и уведомляем подписчиков
            self.settings['language'] = new_lang
            self.save_settings()
            # Заголовок окна и все тексты обновятся через callback refresh_ui_texts

    # === ОСТАЛЬНЫЕ МЕТОДЫ (без изменений) ===

    def refresh_host_apis(self):
        """Обновляет список Host API в комбобоксе"""
        apis = list_host_apis()
        self.host_api_combo['values'] = [api['name'] for api in apis]
        self._host_apis_cache = apis
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
            if apis:
                self.host_api_combo.current(0)
                self.host_api_index = apis[0]['index']
            else:
                self.host_api_combo.set('')
                self.host_api_index = None

    def on_host_api_change(self, event=None):
        selection = self.host_api_combo.current()
        if selection >= 0 and hasattr(self, '_host_apis_cache') and selection < len(self._host_apis_cache):
            self.host_api_index = self._host_apis_cache[selection]['index']
        else:
            self.host_api_index = None
        self.refresh_devices()
        self.save_settings()

    def refresh_devices(self):
        self.audio.stop()
        try:
            sd._terminate()
            sd._initialize()
        except Exception as e:
            logger.warning(f"Error reinitializing sounddevice: {e}")

        devices = list_audio_devices(host_api_index=self.host_api_index)
        current_device_name = self.device_var.get()
        device_names = [dev['name'] for dev in devices]
        self.device_combo['values'] = device_names

        if current_device_name not in device_names:
            logger.warning(
                f"Device '{current_device_name}' no longer available, switching to default")
            self.device_var.set(tr('audio_default_mic'))
            dev_info = next(
                (dev for dev in devices if dev['name'] == tr('audio_default_mic')), None)
            if dev_info:
                self.audio.set_device_by_api(
                    self.host_api_index, dev_info['index'], dev_info.get('is_output', False))
            else:
                self.audio.set_device_by_api(self.host_api_index, None, False)
        else:
            dev_info = next(
                (dev for dev in devices if dev['name'] == current_device_name), None)
            if dev_info:
                self.audio.set_device_by_api(
                    self.host_api_index, dev_info['index'], dev_info.get('is_output', False))
            else:
                self.audio.set_device_by_api(self.host_api_index, None, False)

        self.audio.start()
        logger.info(
            f"Devices refreshed, found {len(device_names)} devices. Active: {self.device_var.get()}")
        self.save_settings()

    def start_device_monitor(self):
        def check():
            if self.initializing:
                self.root.after(5000, check)
                return
            try:
                current_devices = list_audio_devices(
                    host_api_index=self.host_api_index)
                current_names = [dev['name'] for dev in current_devices]
                old_names = self.device_combo['values']
                if set(current_names) != set(old_names):
                    logger.info("Audio device list changed, refreshing...")
                    self.refresh_devices()
            except Exception as e:
                logger.error(f"Error in device monitor: {e}")
            self.root.after(5000, check)
        self.root.after(5000, check)

    def on_device_change(self, event):
        device_name = self.device_var.get()
        devices = list_audio_devices(host_api_index=self.host_api_index)
        dev_info = None
        for dev in devices:
            if dev['name'] == device_name:
                dev_info = dev
                break
        if dev_info:
            self.audio.set_device_by_api(
                self.host_api_index, dev_info['index'], dev_info.get('is_output', False))
        else:
            self.audio.set_device_by_api(self.host_api_index, None, False)
        logger.info(
            f"Audio device changed to: {device_name}. Restarting stream...")
        self.audio.stop()
        time.sleep(0.1)
        self.audio.start()
        self.save_settings()

    def load_preview_for_slot(self, slot_idx):
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
                img = Image.new("RGBA", (85, 85), (0, 0, 0, 0))
                photo = ImageTk.PhotoImage(img)
                self.slot_previews[slot_idx] = photo
                return photo
        img = Image.new("RGBA", (85, 85), (0, 0, 0, 0))
        photo = ImageTk.PhotoImage(img)
        self.slot_previews[slot_idx] = photo
        return photo

    def show_temporary_message(self, title, message, duration_ms=3000):
        popup = tk.Toplevel(self.root)
        popup.title(title)
        popup.transient(self.root)
        popup.resizable(False, False)
        popup.geometry("+%d+%d" % (
            self.root.winfo_rootx() + self.root.winfo_width() // 2 - 150,
            self.root.winfo_rooty() + self.root.winfo_height() // 2 - 50
        ))
        message_label = ttk.Label(popup, text=message, padding=10)
        message_label.pack()
        popup.after(duration_ms, popup.destroy)
        self.root.focus_set()

    def on_audio_level(self, level):
        now = time.time()
        if now - self._last_ui_update < 0.05:
            if self.renderer_was_started:
                self.renderer.set_audio_level(level * self.sensitivity.get())
            return
        try:
            self.audio_level_scaled = level * self.sensitivity.get()
            self.root.after_idle(self._batch_ui_update,
                                 self.audio_level_scaled)
            if self.renderer_was_started:
                self.renderer.set_audio_level(self.audio_level_scaled)
            self._last_ui_update = now
        except Exception as e:
            logger.error(f"Audio level error: {e}")

    def _batch_ui_update(self, level):
        try:
            self.vol_label.config(text=f"{level:.2f}")
            self.update_level_indicator(level)
        except:
            pass

    def refresh_slot_buttons(self):
        for idx in range(6):
            if idx < len(self.model_slots):
                self._update_single_slot(idx)

    def _update_single_slot(self, idx):
        def update():
            try:
                slot_dir = os.path.join(MODELS_DIR, f"slot{idx+1}")
                json_path = os.path.join(slot_dir, "model.json")
                btn = self.model_slots[idx]
                is_current = (idx + 1 == self.current_slot)
                prefix = "★ " if is_current else " "
                if os.path.exists(json_path):
                    try:
                        with open(json_path, "r", encoding="utf-8") as f:
                            model_data = json.load(f)
                        model_name = model_data.get('name', f"{tr('slot')} {idx+1}")
                        btn.config(
                            text=f"{prefix}{tr('slot')} {idx+1}\n{model_name[:15]}")
                    except:
                        btn.config(text=f"{prefix}{tr('slot')} {idx+1}\n{tr('error')}")
                else:
                    btn.config(text=f"{prefix}{tr('slot')} {idx+1}\n{tr('empty_slot')}")
                photo = self.load_preview_for_slot(idx)
                btn.config(image=photo)
                btn.photo = photo
            except Exception as e:
                logger.debug(f"Slot update error: {e}")
        self.root.after(idx * 50, update)

    def open_web_link(self):
        import webbrowser
        try:
            url = f"http://localhost:{self.webserver_port}/"
            webbrowser.open(url)
            logger.info(f"Opened web link: {url}")
        except Exception as e:
            logger.error(f"Error opening web link: {e}")
            messagebox.showerror(tr('error'), tr('open_link_error', error=e))

    def change_port(self):
        if self.webserver and getattr(self.webserver, "is_running", False):
            messagebox.showwarning(
                tr('change_port_warning'), tr('change_port_before_stop'))
            return

        dialog = tk.Toplevel(self.root)
        dialog.title(tr('port_dialog_title'))
        dialog.geometry("300x150")
        dialog.resizable(False, False)
        dialog.transient(self.root)
        dialog.grab_set()
        dialog.update_idletasks()
        x = self.root.winfo_x() + (self.root.winfo_width() // 2) - (300 // 2)
        y = self.root.winfo_y() + (self.root.winfo_height() // 2) - (150 // 2)
        dialog.geometry(f"+{x}+{y}")

        ttk.Label(dialog, text=tr('port_dialog_label'),
                  font=("Arial", 10)).pack(pady=(15, 5))
        port_frame = ttk.Frame(dialog)
        port_frame.pack(pady=10)
        port_var = tk.StringVar(value=str(self.webserver_port))
        port_entry = ttk.Entry(port_frame, textvariable=port_var, width=10, font=(
            "Arial", 12), justify="center")
        port_entry.pack(side="left", padx=5)
        port_entry.select_range(0, tk.END)
        port_entry.focus_set()

        def is_port_available(port):
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
                    messagebox.showerror(
                        tr('error'), tr('port_range'), parent=dialog)
                    return
                if new_port == self.webserver_port:
                    dialog.destroy()
                    return
                if not is_port_available(new_port):
                    for attempt in range(3):
                        if is_port_available(new_port):
                            break
                        time.sleep(0.5)
                    if not is_port_available(new_port):
                        messagebox.showerror(
                            tr('error'), tr('port_busy', port=new_port), parent=dialog)
                        return

                old_port = self.webserver_port
                self.webserver_port = new_port
                self.port_btn.config(text=tr('port_btn', port=self.webserver_port))
                self.webserver = WebServer(
                    self.renderer, port=self.webserver_port)
                self.save_settings()
                self.show_temporary_message(
                    tr('port_changed'), tr('port_changed_msg', old=old_port, new=self.webserver_port))
                dialog.destroy()
            except ValueError:
                messagebox.showerror(
                    tr('error'), tr('port_invalid'), parent=dialog)
            except Exception as e:
                messagebox.showerror(
                    tr('error'), f"{tr('error')}: {e}", parent=dialog)
                logger.error(f"Error changing port: {e}")

        btn_frame = ttk.Frame(dialog)
        btn_frame.pack(pady=15)
        ttk.Button(btn_frame, text=tr('port_dialog_apply'),
                   command=apply_port).pack(side="left", padx=5)
        ttk.Button(btn_frame, text=tr('port_dialog_cancel'), command=dialog.destroy).pack(
            side="left", padx=5)
        dialog.bind("<Return>", lambda e: apply_port())
        self.root.wait_window(dialog)

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

    def _on_wave_scale_move(self, param, value):
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
        self.audio.set_sensitivity(self.sensitivity.get())
        logger.info(f"Sensitivity changed to: {self.sensitivity.get()}")
        self.save_settings()

    def toggle_noise_gate(self):
        enabled = self.noise_gate_enabled.get()
        threshold = self.noise_gate_threshold.get() if enabled else 0.0
        self.audio.noise_gate_threshold = threshold
        self.renderer.set_noise_gate(threshold)
        logger.info(
            f"Noise gate {'enabled' if enabled else 'disabled'} with threshold: {threshold}")
        self.save_settings()

    def update_noise_gate_threshold(self):
        if self.noise_gate_enabled.get():
            threshold = self.noise_gate_threshold.get()
            self.audio.noise_gate_threshold = threshold
            self.renderer.set_noise_gate(threshold)
            logger.info(f"Noise gate threshold updated to: {threshold}")
        self.save_settings()

    def update_wave_params(self):
        self.wave_params = {
            'amplitude': self.wave_amplitude.get(),
            'frequency': self.wave_frequency.get(),
            'speed': self.wave_speed.get()
        }
        wave_enabled = self.wave_effect.get()
        if wave_enabled:
            self.renderer.set_wave(
                True, self.wave_params['amplitude'], self.wave_params['frequency'], self.wave_params['speed'])
        else:
            self.renderer.set_wave(False, 0, 0, 0)
        self.save_settings()

    def update_effects(self):
        effects = {
            'shake': self.shake.get(),
            'bounce': self.bounce.get(),
            'pulse': self.pulse.get(),
            'blink': self.blink.get(),
            'random_effect': self.random_effect.get(),
            'wave': self.wave_effect.get()
        }
        self.renderer.set_effects(effects)
        self.update_wave_params()
        logger.info(f"Effects updated: {effects}")
        self.save_settings()

    def update_idle_setting(self):
        enabled = self.idle_enabled.get()
        timeout = self.idle_timeout.get()
        fade = self.idle_fade_duration.get()
        restore = self.idle_restore_duration.get()
        self.renderer.set_idle(enabled, timeout, fade, restore)
        logger.info(
            f"Idle mode updated: enabled={enabled}, timeout={timeout}, fade={fade}, restore={restore}")
        self.save_settings()

    def load_settings(self):
        if os.path.exists(SETTINGS_FILE):
            try:
                with open(SETTINGS_FILE, 'r') as f:
                    return json.load(f)
            except Exception as e:
                logger.error(f"Error loading settings: {e}")
        return {}

    def save_settings(self):
        if self.initializing:
            return
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
            'host_api_index': self.host_api_index,
            'language': i18n.lang
        }
        try:
            with open(SETTINGS_FILE, 'w') as f:
                json.dump(settings, f, indent=2)
            logger.info("Settings saved automatically")
        except Exception as e:
            logger.error(f"Error saving settings: {e}")

    def toggle_section(self, section_name):
        if section_name == "effects":
            if self.effects_expanded:
                self.effects_content.pack_forget()
                self.effects_expanded = False
                self._update_section_header('effects', False)
            else:
                self.effects_content.pack(fill="x", padx=3, pady=(0, 2))
                self.effects_expanded = True
                self._update_section_header('effects', True)
        elif section_name == "wave":
            if self.wave_expanded:
                self.wave_content.pack_forget()
                self.wave_expanded = False
                self._update_section_header('wave', False)
            else:
                self.wave_content.pack(fill="x", padx=3, pady=(0, 2))
                self.wave_expanded = True
                self._update_section_header('wave', True)
        elif section_name == "idle":
            if self.idle_expanded:
                self.idle_content.pack_forget()
                self.idle_expanded = False
                self._update_section_header('idle', False)
            else:
                self.idle_content.pack(fill="x", padx=3, pady=(0, 2))
                self.idle_expanded = True
                self._update_section_header('idle', True)
        elif section_name == "thresh":
            if self.thresh_expanded:
                self.thresh_content.pack_forget()
                self.thresh_expanded = False
                self._update_section_header('thresh', False)
            else:
                self.thresh_content.pack(fill="x", padx=3, pady=(0, 2))
                self.thresh_expanded = True
                self._update_section_header('thresh', True)
                self.refresh_thresholds_ui()
        elif section_name == "states":
            if self.states_expanded:
                self.states_content.pack_forget()
                self.states_expanded = False
                self._update_section_header('states', False)
            else:
                self.states_content.pack(fill="x", padx=3, pady=(0, 2))
                self.states_expanded = True
                self._update_section_header('states', True)
                self.refresh_states_ui()
        self.save_settings()

    def refresh_thresholds_ui(self):
        for widget in self.thresh_content.winfo_children():
            widget.destroy()
        self.thresh_vars = []
        self.threshold_lines = {}
        self.threshold_labels = {}
        if not hasattr(self.renderer, 'model') or not self.renderer.model:
            ttk.Label(self.thresh_content,
                      text=tr('no_model')).pack(pady=5)
            return
        mouth_states = self.renderer.model.get('mouth_states', [])
        if not mouth_states:
            ttk.Label(self.thresh_content,
                      text=tr('editor_empty')).pack(pady=5)
            return
        thresholds_grid = ttk.Frame(self.thresh_content)
        thresholds_grid.pack(fill="x", padx=2, pady=2)
        row = 0
        col = 0
        for i, state in enumerate(mouth_states):
            state_name = state.get('name', f'State {i+1}')
            var = tk.DoubleVar(value=state.get('threshold', 0.0))
            self.thresh_vars.append(var)
            ttk.Label(thresholds_grid, text=f"{state_name}: ").grid(
                row=row, column=col*2, sticky="w", padx=1, pady=1)
            entry = ttk.Entry(thresholds_grid, textvariable=var, width=6)
            entry.grid(row=row, column=col*2+1, padx=1, pady=1)
            entry.bind("<Return>", lambda e,
                       idx=i: self.update_single_threshold(idx))
            entry.bind("<FocusOut>", lambda e,
                       idx=i: self.update_single_threshold(idx))
            col += 1
            if col >= 2:
                col = 0
                row += 1
        help_label = ttk.Label(
            thresholds_grid, text=tr('editor_state_threshold'), font=("Arial", 7))
        help_label.grid(row=row+1, column=0, columnspan=4, pady=(0, 1))
        self.update_threshold_visuals()

    def refresh_states_ui(self):
        for widget in self.states_content.winfo_children():
            widget.destroy()
        self.state_vars = []
        if not hasattr(self.renderer, 'model') or not self.renderer.model:
            ttk.Label(self.states_content,
                      text=tr('no_model')).pack(pady=5)
            return
        mouth_states = self.renderer.model.get('mouth_states', [])
        if not mouth_states:
            ttk.Label(self.states_content,
                      text=tr('editor_empty')).pack(pady=5)
            return
        states_grid = ttk.Frame(self.states_content)
        states_grid.pack(fill="x", padx=2, pady=2)
        row = 0
        col = 0
        for i, state in enumerate(mouth_states):
            state_name = state.get('name', f'State {i+1}')
            var = tk.BooleanVar(value=state.get('active', True))
            self.state_vars.append(var)
            cb = ttk.Checkbutton(states_grid, text=state_name, variable=var,
                                 command=lambda idx=i: self.update_single_active_state(idx))
            cb.grid(row=row, column=col, sticky="w", padx=3, pady=1)
            col += 1
            if col >= 2:
                col = 0
                row += 1

    def update_single_threshold(self, state_index):
        if not hasattr(self.renderer, 'model') or not self.renderer.model:
            return
        mouth_states = self.renderer.model.get('mouth_states', [])
        if state_index < 0 or state_index >= len(mouth_states):
            return
        try:
            new_threshold = self.thresh_vars[state_index].get()
            new_threshold = max(0.0, min(1.0, new_threshold))
            mouth_states[state_index]['threshold'] = new_threshold
            self.renderer.set_mouth_states(mouth_states)
            self.update_threshold_visuals()
            if self.current_slot:
                self.save_model_to_current_slot()
        except Exception as e:
            logger.error(f"Error updating single threshold: {e}")

    def update_single_active_state(self, state_index):
        if not hasattr(self.renderer, 'model') or not self.renderer.model:
            return
        mouth_states = self.renderer.model.get('mouth_states', [])
        if state_index < 0 or state_index >= len(mouth_states):
            return
        try:
            mouth_states[state_index]['active'] = self.state_vars[state_index].get()
            self.renderer.set_mouth_states(mouth_states)
            self.update_threshold_visuals()
            if self.current_slot:
                self.save_model_to_current_slot()
        except Exception as e:
            logger.error(f"Error updating single active state: {e}")

    def save_model_to_current_slot(self):
        if not self.current_slot or not self.renderer.model:
            return
        try:
            slot_dir = os.path.join(MODELS_DIR, f"slot{self.current_slot}")
            os.makedirs(slot_dir, exist_ok=True)
            json_path = os.path.join(slot_dir, "model.json")
            with open(json_path, "w", encoding="utf-8") as f:
                json.dump(self.renderer.model, f, indent=2, ensure_ascii=False)
            self._update_single_slot(self.current_slot - 1)
            logger.info(f"Model auto-saved to slot {self.current_slot}")
        except Exception as e:
            logger.error(f"Error auto-saving model to slot: {e}")

    def update_threshold_visuals(self):
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
            line_id = self.level_canvas.create_line(pos, 0, pos, 25, dash=(
                2, 2), width=1, fill="#2196F3", tags="threshold_line")
            self.threshold_lines[f"state_{i}"] = line_id
            if i == 0:
                anchor = "w"
            elif i == len(mouth_states) - 1:
                anchor = "e"
            else:
                anchor = "center"
            state_name = state.get('name', f'S{i+1}')[:6]
            self.level_canvas.create_text(
                pos, 7, text=state_name, anchor=anchor, tags="threshold_label", font=("Arial", 7))

    def update_level_indicator(self, level):
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
        self.update_threshold_visuals()
        self.update_level_indicator(self.audio_level_scaled if hasattr(
            self, 'audio_level_scaled') else 0)

    def load_slot(self, idx, silent=False):
        slot_dir = os.path.join(MODELS_DIR, f"slot{idx+1}")
        json_path = os.path.join(slot_dir, "model.json")
        if not os.path.exists(json_path):
            if not silent:
                answer = messagebox.askyesno(
                    tr('no_model'), tr('create_new'))
                if not answer:
                    return
            self.renderer.model = {
                "name": tr('default_model_name'),
                "layers": [],
                "groups": [],
                "mouth_states": [
                    {'id': 0, 'name': tr('default_state_0'), 'threshold': 0.0, 'active': True},
                    {'id': 1, 'name': tr('default_state_1'), 'threshold': 0.3, 'active': True},
                    {'id': 2, 'name': tr('default_state_2'), 'threshold': 0.6, 'active': True},
                    {'id': 3, 'name': tr('default_state_3'), 'threshold': 0.9, 'active': True}
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
                    {'id': 0, 'name': tr('default_state_0'), 'threshold': 0.0, 'active': True},
                    {'id': 1, 'name': tr('default_state_1'), 'threshold': 0.3, 'active': True},
                    {'id': 2, 'name': tr('default_state_2'), 'threshold': 0.6, 'active': True},
                    {'id': 3, 'name': tr('default_state_3'), 'threshold': 0.9, 'active': True}
                ]
            self.renderer.load_model(data, slot_dir)
            if self.webserver:
                self.webserver.renderer = self.renderer

        self.current_slot = idx + 1
        self.refresh_thresholds_ui()
        self.refresh_states_ui()
        self.refresh_slot_buttons()
        self.save_settings()
        model_name = self.renderer.model.get('name', 'model')
        if not silent:
            logger.info(f"Model loaded from slot {idx+1}: {model_name}")
            self.show_temporary_message(
                tr('model_loaded', slot=idx+1), f"{tr('model_loaded', slot=idx+1)}")

    def open_editor(self):
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
            self.editor = editor  # сохраняем ссылку для обновления

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
                        host_api_index=self.host_api_index
                    )
                    self.audio.noise_gate_threshold = self.noise_gate_threshold.get(
                    ) if self.noise_gate_enabled.get() else 0.0
                    self.audio.set_sensitivity(self.sensitivity.get())
                    self.audio.start()
                except Exception as e:
                    logger.error(f"Error restarting audio: {e}")
                editor.destroy()
                self.editor = None
            editor.protocol("WM_DELETE_WINDOW", on_editor_close)
        except Exception as e:
            import traceback
            tb = traceback.format_exc()
            with open("error.log", "w", encoding="utf-8") as f:
                f.write(tb)
            messagebox.showerror(
                tr('error'), tr('open_editor_error', error=e))
            logger.error(f"Error opening editor: {e}\n{tb}")
            self.root.attributes('-disabled', False)

    def on_model_saved(self, model_data, model_dir, slot_num=None):
        self.refresh_slot_buttons()
        logger.info(f"Model saved to directory: {model_dir}")
        if slot_num == self.current_slot:
            logger.info(f"Model saved to current slot {slot_num}, reloading")
            self.load_slot(slot_num - 1, silent=True)

    def toggle_server(self):
        if self.webserver and getattr(self.webserver, "is_running", False):
            self.webserver.stop()
            self.server_btn.config(text=tr('server_btn'))
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
                    self.renderer.set_idle(self.idle_enabled.get(), self.idle_timeout.get(
                    ), self.idle_fade_duration.get(), self.idle_restore_duration.get())
                    self.renderer.set_effects(self.effects)
                    if self.wave_effect.get():
                        self.renderer.set_wave(
                            True, self.wave_params['amplitude'], self.wave_params['frequency'], self.wave_params['speed'])
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
                self.server_btn.config(text=tr('server_stop'))
                self.link_btn.config(state="normal")
                self.port_btn.config(state="disabled")
                logger.info("Web server started")
            except Exception as e:
                logger.error(f"Error starting web server: {e}")
                messagebox.showerror(
                    tr('error'), tr('server_error', error=e))

    def on_close(self):
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
        # Отписываемся от обновлений языка
        i18n.unregister_callback(self.refresh_ui_texts)
        logger.info("Application closed")
        self.root.destroy()


if __name__ == "__main__":
    root = tk.Tk()
    app = App(root)
    root.mainloop()