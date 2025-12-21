import os
import json
import time
import tkinter as tk
from tkinter import ttk, filedialog, simpledialog, messagebox
from PIL import Image, ImageTk, ImageSequence
import shutil
import math
import random
import threading
import sys
import glob
import re
from audio import AudioProcessor
import logging
import logging.handlers
from datetime import datetime
import zipfile
import tempfile
import queue
import weakref
from functools import lru_cache

# Определение базовой директории
if getattr(sys, 'frozen', False):
    BASE_DIR = os.path.dirname(sys.executable)
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# Создание папки для логов
LOGS_DIR = os.path.join(BASE_DIR, "logs")
os.makedirs(LOGS_DIR, exist_ok=True)

# Настройка логирования для editor
def setup_editor_logging():
    logger = logging.getLogger('editor')
    logger.setLevel(logging.DEBUG)
    # Форматирование
    formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    # Файловый обработчик с ротацией
    log_file = os.path.join(LOGS_DIR, 'editor.log')
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
logger = setup_editor_logging()

MODELS_DIR = os.path.join(BASE_DIR, "models")
os.makedirs(MODELS_DIR, exist_ok=True)

class CanvasItem:
    def __init__(self, layer, image_path):
        self.layer = layer
        self.image_path = image_path
        self.is_gif = bool(layer.get("is_gif", False))
        self.scale = float(layer.get("scale", 1.0))
        self.rotation = int(layer.get("rotation", 0))
        self.x = int(layer.get("x", 0))
        self.y = int(layer.get("y", 0))
        self.flip_horizontal = bool(layer.get("flip_horizontal", False))
        self.flip_vertical = bool(layer.get("flip_vertical", False))
        self.visible = bool(layer.get("visible", True))
        
        # Кэширование изображений для разных уровней зума
        self._zoom_cache = {}
        self._max_zoom_cache = 3
        self._preview_size = (1024, 1024)
        
        # Атрибуты для GIF
        self.gif_frames = []
        self.current_frame = 0
        self.last_frame_time = 0
        self.frame_durations = []
        
        # Загружаем оригинальное изображение (уменьшенное для производительности)
        self._original_image = None
        self._load_original_image()
    
    def _load_original_image(self):
        """Загружает и подготавливает изображение для отрисовки"""
        try:
            if self.is_gif:
                with Image.open(self.image_path) as gif:
                    gif.seek(0)
                    img = gif.copy().convert("RGBA")
                    # Уменьшаем только если изображение очень большое
                    if img.width > 1024 or img.height > 1024:
                        img.thumbnail((1024, 1024), Image.LANCZOS)
                    self._original_image = img
                    
                    # Загружаем кадры GIF для анимации
                    self._load_gif_frames()
            else:
                img = Image.open(self.image_path).convert("RGBA")
                if img.width > 1024 or img.height > 1024:
                    img.thumbnail((1024, 1024), Image.LANCZOS)
                self._original_image = img
        except Exception as e:
            logger.error(f"Ошибка загрузки изображения: {e}")
            self._original_image = Image.new("RGBA", (100, 100), (255, 0, 0, 128))
    
    def _load_gif_frames(self):
        """Загружает все кадры GIF"""
        try:
            if self.is_gif:
                with Image.open(self.image_path) as gif:
                    self.gif_frames = []
                    self.frame_durations = []
                    for frame in range(gif.n_frames):
                        gif.seek(frame)
                        frame_img = gif.copy().convert("RGBA")
                        if frame_img.width > 1024 or frame_img.height > 1024:
                            frame_img.thumbnail((1024, 1024), Image.LANCZOS)
                        self.gif_frames.append(frame_img)
                        try:
                            duration = gif.info.get('duration', 100)
                            if duration == 0:
                                duration = 100  # Защита от нулевой длительности
                            self.frame_durations.append(duration / 1000.0)  # Конвертируем в секунды
                        except:
                            self.frame_durations.append(0.1)
                    
                    # Инициализируем таймер
                    self.current_frame = 0
                    self.last_frame_time = time.time()
        except Exception as e:
            logger.error(f"Ошибка загрузки кадров GIF: {e}")
            self.is_gif = False
    
    def get_current_image(self):
        """Возвращает текущий кадр (для GIF) или изображение"""
        if self.is_gif and self.gif_frames:
            now = time.time()
            if now - self.last_frame_time > self.frame_durations[self.current_frame]:
                self.current_frame = (self.current_frame + 1) % len(self.gif_frames)
                self.last_frame_time = now
            return self.gif_frames[self.current_frame]
        return self._original_image
    
    @lru_cache(maxsize=10)
    def _get_transformed_image_cached(self, zoom_level, scale, rotation, flip_h, flip_v, frame_idx=0):
        """Кэшированное получение трансформированного изображения"""
        # Получаем текущее изображение
        if self.is_gif and self.gif_frames:
            img = self.gif_frames[frame_idx % len(self.gif_frames)].copy()
        elif self._original_image:
            img = self._original_image.copy()
        else:
            return Image.new("RGBA", (10, 10), (255, 0, 0, 128))
        
        # ПРИМЕНЯЕМ МАСШТАБ К ОРИГИНАЛЬНОМУ ИЗОБРАЖЕНИЮ
        if scale != 1.0:
            new_width = max(1, int(img.width * scale))
            new_height = max(1, int(img.height * scale))
            img = img.resize((new_width, new_height), Image.LANCZOS)
        
        # Отражение (применяется после масштабирования)
        if flip_h:
            img = img.transpose(Image.FLIP_LEFT_RIGHT)
        if flip_v:
            img = img.transpose(Image.FLIP_TOP_BOTTOM)
        
        # Поворот
        if rotation != 0:
            img = img.rotate(rotation, expand=True, resample=Image.BICUBIC)
        
        # Масштаб зума (для отображения в редакторе)
        if zoom_level != 1.0:
            final_width = max(1, int(img.width * zoom_level))
            final_height = max(1, int(img.height * zoom_level))
            img = img.resize((final_width, final_height), Image.LANCZOS)
        
        return img

    def _get_transformed_image(self, zoom_level=1.0):
        """Получает трансформированное изображение для заданного уровня зума"""
        # Для GIF сначала обновляем текущий кадр
        if self.is_gif and self.gif_frames:
            self._update_gif_frame()
        
        # Проверяем кэш
        cache_key = f"{zoom_level:.2f}_{self.scale}_{self.rotation}_{self.flip_horizontal}_{self.flip_vertical}"
        
        if self.is_gif:
            # Для GIF добавляем номер кадра в ключ кэша
            cache_key += f"_{self.current_frame}"
        
        if cache_key in self._zoom_cache:
            return self._zoom_cache[cache_key]
        
        if not self._original_image and not self.gif_frames:
            return Image.new("RGBA", (10, 10), (255, 0, 0, 128))
        
        # Очищаем старый кэш если слишком много записей
        if len(self._zoom_cache) > self._max_zoom_cache:
            self._zoom_cache.clear()
        
        # Получаем текущее изображение
        if self.is_gif and self.gif_frames:
            # Для GIF используем текущий кадр
            img = self.gif_frames[self.current_frame].copy()
        else:
            img = self._original_image.copy()
        
        # ПРИМЕНЯЕМ МАСШТАБ К ОРИГИНАЛЬНОМУ ИЗОБРАЖЕНИЮ
        if self.scale != 1.0:
            new_width = max(1, int(img.width * self.scale))
            new_height = max(1, int(img.height * self.scale))
            img = img.resize((new_width, new_height), Image.LANCZOS)
        
        # Отражение (применяется после масштабирования)
        if self.flip_horizontal:
            img = img.transpose(Image.FLIP_LEFT_RIGHT)
        if self.flip_vertical:
            img = img.transpose(Image.FLIP_TOP_BOTTOM)
        
        # Поворот
        if self.rotation != 0:
            img = img.rotate(self.rotation, expand=True, resample=Image.BICUBIC)
        
        # Масштаб зума (для отображения в редакторе)
        if zoom_level != 1.0:
            final_width = max(1, int(img.width * zoom_level))
            final_height = max(1, int(img.height * zoom_level))
            img = img.resize((final_width, final_height), Image.LANCZOS)
        
        # Сохраняем в кэш
        self._zoom_cache[cache_key] = img
        return img
    
    def _update_gif_frame(self):
        """Обновляет текущий кадр GIF на основе времени"""
        if not self.is_gif or not self.gif_frames:
            return
        
        now = time.time()
        if now - self.last_frame_time >= self.frame_durations[self.current_frame]:
            self.current_frame = (self.current_frame + 1) % len(self.gif_frames)
            self.last_frame_time = now

    def get_image_for_display(self, zoom_level=1.0):
        """Возвращает изображение для отображения с учетом зума"""
        if not self.visible:
            return None
        return self._get_transformed_image(zoom_level)
    
    def clear_cache(self):
        """Очищает кэш изображений"""
        self._get_transformed_image_cached.cache_clear()
        self._zoom_cache.clear()

class ModelEditor(tk.Toplevel):
    def __init__(self, master, on_save=None, device='По умолчанию', noise_gate_threshold=0.01, sensitivity=1.0, thresholds=None, current_slot=None):
        super().__init__(master)
        self.title("Редактор моделей")
        self.geometry("1400x800")
        self.on_save = on_save
        self.protocol("WM_DELETE_WINDOW", self.on_close)
        self.current_slot = current_slot
        logger.info(f"Model editor opened, current slot: {current_slot}")
        
        # Сохраняем настройки микрофона
        self.mic_device = device
        self.mic_noise_gate_threshold = noise_gate_threshold
        self.mic_sensitivity = sensitivity
        self.thresholds = thresholds or {
            'silent': 0.05,
            'whisper': 0.25,
            'normal': 0.6,
            'shout': 0.8
        }
        
        # Данные модели
        self.model = {"name": "Без названия", "layers": [], "groups": [], "width": 700, "height": 700}
        self.model_dir = None
        self.original_slot = None
        self.items = []
        self.imported_files = []
        self.drag_data = {"item": None, "x": 0, "y": 0, "group_items": []}
        self.selected_group = None
        self.current_selection = []
        self.preview_fps = 30
        self.last_autosave = time.time()
        self.autosave_interval = 5.0
        self.audio_level = 0.0
        self.blink_preview_running = False
        
        # ОПТИМИЗАЦИИ
        self._redraw_scheduled = False
        self._redraw_delay = 33  # ~30 FPS для редактора
        self._canvas_cache_valid = False
        self._last_redraw_time = 0
        self._photo_images = {}  # Кэш PhotoImage
        self._photo_cache_limit = 20
        
        # Кэширование изображений для дерева
        self._tree_image_cache = {}
        
        # Оптимизация событий мыши
        self._last_mouse_event = 0
        self._mouse_event_throttle = 0.016  # ~60 FPS для событий мыши
        
        # Система отмены/повтора
        self.history = []
        self.history_index = -1
        self.max_history_size = 50
        
        # Настройки холста
        self.canvas_width = 700
        self.canvas_height = 700
        
        # Настройки зума и просмотра
        self.zoom_level = 1.0
        self.zoom_step = 0.1
        self.min_zoom = 0.1
        self.max_zoom = 3.0  # Уменьшаем максимальный зум
        self.offset_x = 0
        self.offset_y = 0
        self.is_panning = False
        self.last_pan_x = 0
        self.last_pan_y = 0
        
        # Таймеры для анимаций
        self.group_blink_timers = {}
        self.group_blink_until = {}
        self.group_random_timers = {}
        self.group_random_current = {}
        
        # Состояние дерева для сохранения
        self.tree_state = {
            "expanded_groups": set(),
            "selected_items": set(),
            "preserve_selection": False
        }
        
        # Очищаем старые временные папки при запуске
        self.cleanup_old_temp_folders()
        
        # Создаем UI
        self._create_ui()
        
        # Сохраняем начальное состояние в историю
        self.save_to_history()
        
        # Запуск превью
        self.after(100, self._preview_loop)
    
    def _create_ui(self):
        """Создание пользовательского интерфейса"""
        main_frame = ttk.Frame(self)
        main_frame.pack(fill=tk.BOTH, expand=True, padx=6, pady=6)
        main_frame.columnconfigure(1, weight=1)
        main_frame.rowconfigure(0, weight=1)
        
        # Левая панель
        left_width = 300
        left = ttk.Frame(main_frame, width=left_width)
        left.grid(row=0, column=0, sticky="ns", padx=(0, 6), pady=0)
        left.grid_propagate(False)
        
        # Центральная панель
        center = ttk.Frame(main_frame)
        center.grid(row=0, column=1, sticky="nsew", padx=0, pady=0)
        
        # Правая панель
        right = ttk.Frame(main_frame, width=350)
        right.grid(row=0, column=2, sticky="ns", padx=(6, 0), pady=0)
        
        # ---- Левая панель ----
        # Имя модели
        name_frame = ttk.LabelFrame(left, text="Имя модели")
        name_frame.pack(fill="x", pady=2)
        
        self.model_name_var = tk.StringVar(value="Без названия")
        name_entry = ttk.Entry(name_frame, textvariable=self.model_name_var)
        name_entry.pack(fill="x", padx=5, pady=5)
        name_entry.bind("<Return>", self.update_model_name)
        
        # Панель с кнопками управления моделью
        button_row1 = ttk.Frame(left)
        button_row1.pack(fill="x", pady=2)
        ttk.Button(button_row1, text="Новая", command=self.new_model, width=10).pack(side="left", padx=1, fill="x", expand=True)
        ttk.Button(button_row1, text="Загрузить", command=self.load_model, width=10).pack(side="left", padx=1, fill="x", expand=True)
        ttk.Button(button_row1, text="Сохранить", command=self.save_model, width=10).pack(side="left", padx=1, fill="x", expand=True)
        
        button_row2 = ttk.Frame(left)
        button_row2.pack(fill="x", pady=2)
        ttk.Button(button_row2, text="Импорт PNG/GIF", command=self.import_images, width=15).pack(side="left", padx=1, fill="x", expand=True)
        
        button_row3 = ttk.Frame(left)
        button_row3.pack(fill="x", pady=2)
        ttk.Button(button_row3, text="Экспорт ZIP", command=self.export_zip, width=10).pack(side="left", padx=1, fill="x", expand=True)
        ttk.Button(button_row3, text="Импорт ZIP", command=self.import_zip, width=10).pack(side="left", padx=1, fill="x", expand=True)
        
        # Кнопка удаления модели
        ttk.Button(left, text="Удалить модель", command=self.delete_model).pack(fill="x", pady=2)
        
        # Настройки холста
        canvas_frame = ttk.LabelFrame(left, text="Настройки холста")
        canvas_frame.pack(fill="x", pady=10)
        
        ttk.Label(canvas_frame, text="Ширина:").grid(row=0, column=0, sticky="w", padx=2, pady=2)
        self.canvas_width_var = tk.IntVar(value=self.canvas_width)
        width_entry = ttk.Entry(canvas_frame, textvariable=self.canvas_width_var, width=8)
        width_entry.grid(row=0, column=1, sticky="ew", padx=2, pady=2)
        width_entry.bind("<Return>", self.update_canvas_size)
        
        ttk.Label(canvas_frame, text="Высота:").grid(row=1, column=0, sticky="w", padx=2, pady=2)
        self.canvas_height_var = tk.IntVar(value=self.canvas_height)
        height_entry = ttk.Entry(canvas_frame, textvariable=self.canvas_height_var, width=8)
        height_entry.grid(row=1, column=1, sticky="ew", padx=2, pady=2)
        height_entry.bind("<Return>", self.update_canvas_size)
        
        ttk.Button(canvas_frame, text="Применить", command=self.update_canvas_size).grid(row=2, column=0, columnspan=2, pady=5)
        
        # Управление зумом
        zoom_frame = ttk.Frame(canvas_frame)
        zoom_frame.grid(row=3, column=0, columnspan=2, sticky="ew", pady=5)
        ttk.Button(zoom_frame, text="+", width=3, command=self.zoom_in).pack(side="left", padx=2)
        ttk.Button(zoom_frame, text="-", width=3, command=self.zoom_out).pack(side="left", padx=2)
        ttk.Button(zoom_frame, text="Сброс", width=6, command=self.zoom_reset).pack(side="left", padx=2)
        
        # Режим тестирования
        test_frame = ttk.LabelFrame(left, text="Режим тестирования")
        test_frame.pack(fill="x", pady=10)
        
        self.test_mode_var = tk.StringVar(value="none")
        ttk.Radiobutton(test_frame, text="Микрофон", variable=self.test_mode_var, 
                       value="microphone", command=self.update_test_mode).pack(anchor="w")
        ttk.Radiobutton(test_frame, text="Выкл", variable=self.test_mode_var, 
                       value="none", command=self.update_test_mode).pack(anchor="w")
        
        # Индикатор уровня
        self.level_frame = ttk.Frame(test_frame)
        self.level_frame.pack(fill="x", pady=5)
        ttk.Label(self.level_frame, text="Уровень:").pack(side="left")
        self.level_bar = ttk.Progressbar(self.level_frame, length=200, mode="determinate")
        self.level_bar.pack(side="left", fill="x", expand=True, padx=5)
        
        # Аудиопроцессор
        self.audio_processor = AudioProcessor(
            callback=self.on_audio_level,
            device=self.mic_device
        )
        self.audio_processor.noise_gate_threshold = self.mic_noise_gate_threshold
        self.audio_processor.set_sensitivity(self.mic_sensitivity)
        
        ttk.Label(left, text="Импортированные изображения:").pack(anchor="w", pady=(8, 0))
        
        # Список импортированных изображений
        import_frame = ttk.Frame(left)
        import_frame.pack(fill="both", expand=True)
        self.import_canvas = tk.Canvas(import_frame, width=280, height=250)
        self.import_vscroll = ttk.Scrollbar(import_frame, orient="vertical", command=self.import_canvas.yview)
        self.import_canvas.configure(yscrollcommand=self.import_vscroll.set)
        self.import_vscroll.pack(side="right", fill="y")
        self.import_canvas.pack(side="left", fill="both", expand=True)
        
        self.import_inner = ttk.Frame(self.import_canvas)
        self.import_canvas.create_window((0, 0), window=self.import_inner, anchor="nw")
        self.import_inner.bind("<Configure>", lambda e: self.import_canvas.configure(scrollregion=self.import_canvas.bbox("all")))
        
        # ---- Центральная панель ----
        # Заголовок предпросмотра с кнопками отмены/повтора
        preview_header = ttk.Frame(center)
        preview_header.pack(fill="x", padx=5, pady=(0, 5))
        
        ttk.Label(preview_header, text="Предпросмотр", font=("Arial", 10, "bold")).pack(side="left", padx=(0, 10))
        
        # Кнопки отмены/повтора
        undo_btn = ttk.Button(preview_header, text="←", width=2, command=self.undo)
        undo_btn.pack(side="left", padx=2)
        redo_btn = ttk.Button(preview_header, text="→", width=2, command=self.redo)
        redo_btn.pack(side="left", padx=2)
        
        preview_frame = ttk.Frame(center)
        preview_frame.pack(fill="both", expand=True)
        
        # Создаем основной canvas для отображения
        self.canvas = tk.Canvas(preview_frame, bg="#222", cursor="crosshair")
        self.canvas.pack(fill="both", expand=True, padx=5, pady=5)
        
        # Привязка событий
        self.canvas.bind("<ButtonPress-1>", self.on_canvas_mouse_down)
        self.canvas.bind("<B1-Motion>", self.on_canvas_mouse_move)
        self.canvas.bind("<ButtonRelease-1>", self.on_canvas_mouse_up)
        self.canvas.bind("<MouseWheel>", self.on_canvas_zoom)
        self.canvas.bind("<Button-4>", self.on_canvas_zoom)
        self.canvas.bind("<Button-5>", self.on_canvas_zoom)
        self.canvas.bind("<ButtonPress-2>", self.on_canvas_pan_start)
        self.canvas.bind("<B2-Motion>", self.on_canvas_pan_move)
        self.bind("<Configure>", self.on_window_resize)
        
        # Привязка горячих клавиш для отмены/повтора
        self.bind_all("<Control-z>", self.undo)
        self.bind_all("<Control-y>", self.redo)
        
        # ---- Правая панель ----
        notebook = ttk.Notebook(right)
        notebook.pack(fill="both", expand=True)
        
        # Вкладка 1: Управление элементами
        items_tab = ttk.Frame(notebook)
        notebook.add(items_tab, text="Элементы")
        
        # Вкладка 2: Логика групп
        groups_tab = ttk.Frame(notebook)
        notebook.add(groups_tab, text="Логика групп")
        
        # ---- Вкладка "Элементы" ----
        items_frame = ttk.LabelFrame(items_tab, text="Элементы холста")
        items_frame.pack(fill="both", expand=True, padx=5, pady=5)
        
        # Древовидный список элементов
        items_list_frame = ttk.Frame(items_frame)
        items_list_frame.pack(fill="both", expand=True, pady=(0, 5))
        
        # Создаем Treeview с вертикальной прокруткой и множественным выбором
        self.tree = ttk.Treeview(items_list_frame, show="tree", height=15, selectmode='extended')
        scrollbar = ttk.Scrollbar(items_list_frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y")
        self.tree.pack(side="left", fill="both", expand=True)
        self.tree.bind("<<TreeviewSelect>>", self.on_tree_select)
        
        # Кнопки управления
        btns_frame = ttk.Frame(items_frame)
        btns_frame.pack(fill="x", pady=(0, 10))
        ttk.Button(btns_frame, text="↑", width=3, command=self.bring_forward).pack(side="left", padx=2)
        ttk.Button(btns_frame, text="↓", width=3, command=self.send_backward).pack(side="left", padx=2)
        ttk.Button(btns_frame, text="Группа", command=self.group_selected).pack(side="left", padx=2, fill="x", expand=True)
        ttk.Button(btns_frame, text="Разгруппировать", command=self.ungroup_selected).pack(side="left", padx=2, fill="x", expand=True)
        
        # Свойства элемента
        props = ttk.LabelFrame(items_frame, text="Свойства элемента")
        props.pack(fill="x", pady=(0, 5))
        
        grid_frame = ttk.Frame(props)
        grid_frame.pack(fill="x", padx=5, pady=5)
        
        ttk.Label(grid_frame, text="Имя:").grid(row=0, column=0, sticky="w", padx=2, pady=2)
        self.name_entry = ttk.Entry(grid_frame)
        self.name_entry.grid(row=0, column=1, sticky="ew", padx=2, pady=2)
        self.name_entry.bind("<FocusIn>", lambda e: "break")
        
        ttk.Label(grid_frame, text="X:").grid(row=1, column=0, sticky="w", padx=2, pady=2)
        self.x_entry = ttk.Entry(grid_frame)
        self.x_entry.grid(row=1, column=1, sticky="ew", padx=2, pady=2)
        self.x_entry.bind("<Return>", self.apply_props_from_entry)
        self.x_entry.bind("<FocusIn>", lambda e: "break")
        
        ttk.Label(grid_frame, text="Y:").grid(row=2, column=0, sticky="w", padx=2, pady=2)
        self.y_entry = ttk.Entry(grid_frame)
        self.y_entry.grid(row=2, column=1, sticky="ew", padx=2, pady=2)
        self.y_entry.bind("<Return>", self.apply_props_from_entry)
        self.y_entry.bind("<FocusIn>", lambda e: "break")
        
        ttk.Label(grid_frame, text="Масштаб:").grid(row=3, column=0, sticky="w", padx=2, pady=2)
        self.scale_entry = ttk.Entry(grid_frame)
        self.scale_entry.grid(row=3, column=1, sticky="ew", padx=2, pady=2)
        self.scale_entry.bind("<Return>", self.apply_props_from_entry)
        self.scale_entry.bind("<FocusIn>", lambda e: "break")
        
        ttk.Label(grid_frame, text="Поворот:").grid(row=4, column=0, sticky="w", padx=2, pady=2)
        self.rotation_entry = ttk.Entry(grid_frame)
        self.rotation_entry.grid(row=4, column=1, sticky="ew", padx=2, pady=2)
        self.rotation_entry.bind("<Return>", self.apply_props_from_entry)
        self.rotation_entry.bind("<FocusIn>", lambda e: "break")
        
        # Зеркалирование
        mirror_frame = ttk.Frame(props)
        mirror_frame.pack(fill="x", padx=5, pady=2)
        ttk.Label(mirror_frame, text="Зеркалирование:").pack(side="left")
        self.flip_h_var = tk.BooleanVar(value=False)
        self.flip_v_var = tk.BooleanVar(value=False)
        self.flip_h_cb = ttk.Checkbutton(mirror_frame, text="Гор.", variable=self.flip_h_var, 
                       command=self.on_mirror_change)
        self.flip_h_cb.pack(side="left", padx=5)
        self.flip_v_cb = ttk.Checkbutton(mirror_frame, text="Верт.", variable=self.flip_v_var,
                       command=self.on_mirror_change)
        self.flip_v_cb.pack(side="left", padx=5)
        
        # ВИДИМОСТЬ
        self.visible_var = tk.BooleanVar(value=True)
        self.visible_cb = ttk.Checkbutton(props, text="Видимый", variable=self.visible_var,
                                         command=self.on_visible_change)
        self.visible_cb.pack(anchor="w", padx=5, pady=(0, 5))
        
        ttk.Button(props, text="Применить к выбранному", command=self.apply_props).pack(fill="x", padx=5, pady=5)
        
        # ---- Вкладка "Логика групп" ----
        groups_frame = ttk.LabelFrame(groups_tab, text="Логика групп")
        groups_frame.pack(fill="both", expand=True, padx=5, pady=5)
        
        # Выбранная группа
        group_info_frame = ttk.Frame(groups_frame)
        group_info_frame.pack(fill="x", pady=(0, 10))
        ttk.Label(group_info_frame, text="Выбранная группа:").pack(side="left", padx=(0, 5))
        self.group_label = ttk.Label(group_info_frame, text="(нет группы)", font=("Arial", 9, "bold"))
        self.group_label.pack(side="left")
        
        # Настройки логики
        logic_frame = ttk.LabelFrame(groups_frame, text="Состояние → Слой/Группа")
        logic_frame.pack(fill="x", pady=(0, 10))
        
        self.state_vars = {
            "silent": tk.StringVar(value=""),
            "whisper": tk.StringVar(value=""),
            "normal": tk.StringVar(value=""),
            "shout": tk.StringVar(value=""),
            "blink": tk.StringVar(value=""),
            "open": tk.StringVar(value="")
        }
        
        # Настройки состояний
        states = {
            "silent": "Тишина",
            "whisper": "Шёпот",
            "normal": "Норма",
            "shout": "Крик",
            "blink": "Моргание",
            "open": "Открыто"
        }
        
        for i, s in enumerate(states.keys()):
            row = ttk.Frame(logic_frame)
            row.pack(fill="x", padx=5, pady=2)
            ttk.Label(row, text=states[s] + ":", width=10).pack(side="left")
            initial_value = ""
            om = ttk.OptionMenu(row, self.state_vars[s], initial_value)
            om.pack(side="left", fill="x", expand=True)
            setattr(self, f"{s}_menu", om)
        
        # Случайный эффект
        random_frame = ttk.LabelFrame(groups_tab, text="Случайный эффект")
        random_frame.pack(fill="x", pady=(0, 10))
        
        self.random_effect_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(random_frame, text="Случайная смена состояний", 
                        variable=self.random_effect_var).pack(anchor="w", padx=5, pady=2)
        
        interval_frame = ttk.Frame(random_frame)
        interval_frame.pack(fill="x", padx=5, pady=2)
        ttk.Label(interval_frame, text="Интервал:").pack(side="left")
        self.random_min_var = tk.DoubleVar(value=5.0)
        ttk.Entry(interval_frame, textvariable=self.random_min_var, width=5).pack(side="left", padx=2)
        ttk.Label(interval_frame, text="до").pack(side="left")
        self.random_max_var = tk.DoubleVar(value=10.0)
        ttk.Entry(interval_frame, textvariable=self.random_max_var, width=5).pack(side="left", padx=2)
        ttk.Label(interval_frame, text="сек").pack(side="left")
        
        # Настройки моргания
        blink_frame = ttk.LabelFrame(groups_tab, text="Настройки моргания")
        blink_frame.pack(fill="x", pady=(0, 10))
        
        freq_frame = ttk.Frame(blink_frame)
        freq_frame.pack(fill="x", padx=5, pady=2)
        ttk.Label(freq_frame, text="Частота:").pack(side="left")
        self.blink_freq = tk.DoubleVar(value=0.0)
        self.blink_freq_entry = ttk.Entry(freq_frame, width=6, textvariable=self.blink_freq)
        self.blink_freq_entry.pack(side="left", padx=2)
        ttk.Label(freq_frame, text="сек (0=выкл)").pack(side="left", padx=(5, 0))
        
        btn_frame = ttk.Frame(blink_frame)
        btn_frame.pack(fill="x", padx=5, pady=(0, 5))
        ttk.Button(btn_frame, text="Превью", width=8, command=self.show_blink_preview).pack(side="left", padx=2)
        ttk.Button(btn_frame, text="Стоп", command=self.stop_blink_preview).pack(side="left", padx=2)
        
        # Кнопка применения
        ttk.Button(groups_frame, text="Применить логику", command=self.apply_group_logic).pack(fill="x", pady=10)
        
        try:
            self.iconbitmap(os.path.join(BASE_DIR, 'favicon.ico'))
        except Exception as e:
            logger.error(f"Error loading icon: {e}")
    
    def _get_groups_recursive(self, parent_name=None):
        """Получает все группы рекурсивно, начиная с указанной родительской группы"""
        if parent_name is None:
            # Получаем корневые группы (без родителя)
            return [g for g in self.model.get("groups", []) if not g.get("parent")]
        
        # Получаем дочерние группы для указанной родительской группы
        return [g for g in self.model.get("groups", []) if g.get("parent") == parent_name]
    
    def _get_current_state_for_group(self, group_name):
        """Определяет текущее состояние для группы с учетом иерархии"""
        group = next((g for g in self.model.get("groups", []) if g.get("name") == group_name), None)
        if not group:
            return None
            
        logic = group.get("logic", {})
        now = time.time()
        
        # Обработка моргания
        if self.test_mode_var.get() != "none":
            blink_freq = float(group.get("blink_freq", 0.0))
            if group_name not in self.group_blink_timers:
                self.group_blink_timers[group_name] = now + random.uniform(2.0, 6.0)
                self.group_blink_until[group_name] = 0.0
                
            if blink_freq > 0.001:
                if now > self.group_blink_timers.get(group_name, 0):
                    self.group_blink_until[group_name] = now + 0.12
                    self.group_blink_timers[group_name] = now + blink_freq
                    
                if now < self.group_blink_until.get(group_name, 0):
                    if "blink" in logic and logic["blink"]:
                        return logic["blink"]
                    else:
                        # Автоматический поиск слоя для моргания
                        for layer in self.model.get("layers", []):
                            if layer.get("group") == group_name:
                                name = layer.get("name", "").lower()
                                if any(kw in name for kw in ["close", "closed", "shut", "blink", "морг", "закр"]):
                                    return layer.get("name")
        
        # Обработка случайного эффекта
        if group.get("random_effect", False):
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
                    # Фильтруем доступные слои (только непосредственные дети группы)
                    available = []
                    for child_name in children:
                        # Проверяем, является ли child_name слоем или группой
                        if child_name in [l.get("name") for l in self.model.get("layers", [])]:
                            # Это слой
                            if child_name != blink_layer and child_name != open_layer:
                                available.append(child_name)
                        else:
                            # Это группа, проверяем есть ли в ней видимые слои
                            child_group = next((g for g in self.model.get("groups", []) if g.get("name") == child_name), None)
                            if child_group and child_group.get("random_effect", False):
                                available.append(child_name)
                    
                    if available:
                        chosen = random.choice(available)
                        self.group_random_current[group_name] = chosen
            
            interval = random.uniform(min_time, max_time)
            self.group_random_timers[group_name] = now + interval
            
        if self.group_random_current.get(group_name):
            return self.group_random_current.get(group_name)
        
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
            
        # Fallback: если состояния нет в логике, пробуем другие варианты
        if current_state in logic and logic[current_state]:
            return logic[current_state]
        elif "open" in logic and logic["open"]:
            return logic["open"]
        elif "normal" in logic and logic["normal"]:
            return logic["normal"]
        elif "silent" in logic and logic["silent"]:
            return logic["silent"]
            
        # Если ничего не подошло, возвращаем первый доступный слой из группы
        for layer in self.model.get("layers", []):
            if layer.get("group") == group_name and layer.get("visible", True):
                return layer.get("name")
                
        return None
    
    def _get_all_group_items(self, group_name):
        """Получает все элементы из указанной группы и её дочерних групп"""
        items = []
        
        # Получаем непосредственные элементы группы
        for ci in self.items:
            if ci.layer.get("group") == group_name:
                items.append(ci)
        
        # Получаем дочерние группы
        child_groups = [g for g in self.model.get("groups", []) if g.get("parent") == group_name]
        for child_group in child_groups:
            items.extend(self._get_all_group_items(child_group.get("name")))
            
        return items
    
    def _get_visible_items_for_state(self, current_state):
        """Возвращает список видимых элементов для текущего состояния с учетом иерархии"""
        visible_items = []
        processed_groups = set()
        
        # Функция для рекурсивной обработки групп
        def process_group(group_name):
            if group_name in processed_groups:
                return
            processed_groups.add(group_name)
            
            # Получаем группу
            group = next((g for g in self.model.get("groups", []) if g.get("name") == group_name), None)
            if not group:
                return
                
            # Получаем текущее состояние для группы
            chosen = self._get_current_state_for_group(group_name)
            
            if not chosen:
                # Если ничего не выбрано, показываем все видимые слои группы
                for ci in self.items:
                    if ci.layer.get("group") == group_name and ci.visible:
                        visible_items.append(ci)
                return
                
            # Проверяем, является ли chosen группой или слоем
            is_group = any(g.get("name") == chosen for g in self.model.get("groups", []))
            if is_group:
                # Если это группа - рекурсивно обрабатываем ее
                process_group(chosen)
            else:
                # Если это слой - добавляем только его в видимые
                for ci in self.items:
                    if ci.layer.get("name") == chosen and ci.layer.get("group") == group_name and ci.visible:
                        visible_items.append(ci)
        
        # Обрабатываем корневые группы (без родителя)
        root_groups = [g.get("name") for g in self.model.get("groups", []) if not g.get("parent")]
        for group_name in root_groups:
            process_group(group_name)
            
        # Добавляем элементы без групп
        for ci in self.items:
            if not ci.layer.get("group") and ci.visible:
                visible_items.append(ci)
                
        return visible_items
    
    def save_to_history(self, description=""):
        """Сохраняет текущее состояние в историю"""
        if self.history_index < len(self.history) - 1:
            self.history = self.history[:self.history_index + 1]
        
        state = {
            'model': json.dumps(self.model, ensure_ascii=False),
            'items': [],
            'description': description
        }
        
        for ci in self.items:
            item_state = {
                'layer': ci.layer.copy(),
                'x': ci.x,
                'y': ci.y,
                'scale': ci.scale,
                'rotation': ci.rotation,
                'flip_horizontal': ci.flip_horizontal,
                'flip_vertical': ci.flip_vertical,
                'visible': ci.visible,
                'is_gif': ci.is_gif,
                'image_path': ci.image_path
            }
            state['items'].append(item_state)
        
        self.history.append(state)
        self.history_index = len(self.history) - 1
        
        if len(self.history) > self.max_history_size:
            self.history.pop(0)
            self.history_index -= 1
        
        logger.info(f"History saved: {description}, index: {self.history_index}, size: {len(self.history)}")
    
    def get_slot_preview_image(self, slot_num):
        """Получение изображения превью для слота"""
        try:
            preview_path = os.path.join(MODELS_DIR, f"slot{slot_num}", "preview.png")
            if os.path.exists(preview_path):
                img = Image.open(preview_path)
                img.thumbnail((100, 100), Image.LANCZOS)
                return ImageTk.PhotoImage(img)
        except Exception as e:
            logger.error(f"Error loading preview for slot {slot_num}: {e}")
        
        img = Image.new("RGBA", (100, 100), (0, 0, 0, 0))
        return ImageTk.PhotoImage(img)
    
    def get_current_preview_image(self):
        """Получение превью текущей модели"""
        try:
            if self.model_dir:
                preview_path = os.path.join(self.model_dir, "preview.png")
                if os.path.exists(preview_path):
                    img = Image.open(preview_path)
                    img.thumbnail((150, 150), Image.LANCZOS)
                    return ImageTk.PhotoImage(img)
        except Exception as e:
            logger.error(f"Error loading current preview: {e}")
        
        img = Image.new("RGBA", (150, 150), (0, 0, 0, 0))
        return ImageTk.PhotoImage(img)
    
    def undo(self, event=None):
        """Отмена последнего действия"""
        if self.history_index > 0:
            self.history_index -= 1
            self.load_from_history()
            logger.info(f"Undo to index: {self.history_index}")
    
    def redo(self, event=None):
        """Повтор отмененного действия"""
        if self.history_index < len(self.history) - 1:
            self.history_index += 1
            self.load_from_history()
            logger.info(f"Redo to index: {self.history_index}")
    
    def load_from_history(self):
        """Загружает состояние из истории"""
        if self.history_index < 0 or self.history_index >= len(self.history):
            return
        
        state = self.history[self.history_index]
        
        try:
            self.model = json.loads(state['model'])
            
            self.items = []
            for item_state in state['items']:
                ci = CanvasItem(item_state['layer'], item_state['image_path'])
                ci.x = item_state['x']
                ci.y = item_state['y']
                ci.scale = item_state['scale']
                ci.rotation = item_state['rotation']
                ci.flip_horizontal = item_state['flip_horizontal']
                ci.flip_vertical = item_state['flip_vertical']
                ci.visible = item_state['visible']
                ci.is_gif = item_state['is_gif']
                self.items.append(ci)
            
            self.model_name_var.set(self.model.get("name", "Без названия"))
            self.canvas_width = self.model.get("width", 700)
            self.canvas_height = self.model.get("height", 700)
            self.canvas_width_var.set(self.canvas_width)
            self.canvas_height_var.set(self.canvas_height)
            
            self.imported_files.clear()
            for f in os.listdir(self.model_dir) if self.model_dir else []:
                if f.lower().endswith((".png", ".gif")):
                    try:
                        fp = os.path.join(self.model_dir, f)
                        with Image.open(fp) as img:
                            is_gif = img.format == "GIF" and img.is_animated
                            img.seek(0)
                            preview_img = img.copy().convert("RGBA")
                            preview_img.thumbnail((50, 50), Image.LANCZOS)
                        self.imported_files.append((f, preview_img, is_gif))
                    except Exception as e:
                        logger.error(f"Error loading imported file: {e}")
            
            self.refresh_import_list()
            self.refresh_tree()
            self._canvas_cache_valid = False
            self.redraw_canvas()
            
        except Exception as e:
            logger.error(f"Error loading from history: {e}")
    
    def delete_model(self):
        """Удаление текущей модели"""
        if not self.model_dir:
            messagebox.showwarning("Нет модели", "Нет загруженной модели для удаления")
            return
        
        slot_info = ""
        if self.original_slot:
            slot_info = f" из слота {self.original_slot}"
        
        confirm = messagebox.askyesno(
            "Удаление модели", 
            f"Вы уверены, что хотите удалить модель{slot_info}?\n\n"
            "Это действие нельзя отменить!"
        )
        
        if not confirm:
            return
        
        try:
            if self.original_slot:
                slot_dir = os.path.join(MODELS_DIR, f"slot{self.original_slot}")
                if os.path.exists(slot_dir):
                    shutil.rmtree(slot_dir)
                    logger.info(f"Deleted model from slot {self.original_slot}")
            
            self.model = {"name": "Без названия", "layers": [], "groups": [], "width": 700, "height": 700}
            self.model_dir = None
            self.original_slot = None
            self.items.clear()
            self.imported_files.clear()
            self.model_name_var.set("Без названия")
            self.canvas_width = 700
            self.canvas_height = 700
            self.canvas_width_var.set(700)
            self.canvas_height_var.set(700)
            
            self.tree_state["expanded_groups"].clear()
            self.tree_state["selected_items"].clear()
            self.tree_state["preserve_selection"] = False
            
            self.refresh_tree()
            self.refresh_import_list()
            self.zoom_reset()
            self._canvas_cache_valid = False
            self.redraw_canvas()
            
            if hasattr(self.master, 'app') and hasattr(self.master.app, 'refresh_slot_buttons'):
                self.master.app.refresh_slot_buttons()
            
            messagebox.showinfo("Модель удалена", f"Модель удалена{slot_info}")
            
        except Exception as e:
            logger.error(f"Error deleting model: {e}")
            messagebox.showerror("Ошибка", f"Не удалось удалить модель: {e}")
    
    def export_zip(self):
        """Экспорт модели в ZIP архив с выбором места сохранения"""
        try:
            from utils import export_model_zip
        except Exception as e:
            messagebox.showerror("Ошибка экспорта", f"Не удалось импортировать утилиту экспорта: {e}")
            logger.error(f"Error importing export utility: {e}")
            return
            
        if not self.model:
            messagebox.showwarning("Нет модели", "Сначала создайте или загрузите модель")
            return
        
        default_name = f"{self.model.get('name', 'model').replace(' ', '_')}.zip"
        zip_path = filedialog.asksaveasfilename(
            title="Сохранить модель как ZIP",
            defaultextension=".zip",
            filetypes=[("ZIP архивы", "*.zip"), ("Все файлы", "*.*")],
            initialfile=default_name
        )
        
        if not zip_path:
            return
            
        try:
            with tempfile.TemporaryDirectory(prefix="model_export_") as temp_dir:
                model_data = {
                    "name": self.model_name_var.get(),
                    "width": self.canvas_width,
                    "height": self.canvas_height,
                    "layers": [],
                    "groups": self.model.get("groups", [])
                }
                
                for ci in self.items:
                    layer = ci.layer
                    layer_data = {
                        "name": layer.get("name", ""),
                        "file": os.path.basename(layer.get("file", "")),
                        "x": int(ci.x),
                        "y": int(ci.y),
                        "visible": bool(ci.visible),
                        "is_gif": ci.is_gif,
                        "scale": float(ci.scale),
                        "rotation": int(ci.rotation),
                        "group": layer.get("group", None),
                        "flip_horizontal": bool(ci.flip_horizontal),
                        "flip_vertical": bool(ci.flip_vertical)
                    }
                    model_data["layers"].append(layer_data)
                    
                    if hasattr(ci, 'image_path') and os.path.exists(ci.image_path):
                        filename = os.path.basename(ci.image_path)
                        dst = os.path.join(temp_dir, filename)
                        shutil.copy2(ci.image_path, dst)
                
                json_path = os.path.join(temp_dir, "model.json")
                with open(json_path, "w", encoding="utf-8") as f:
                    json.dump(model_data, f, indent=2, ensure_ascii=False)
                
                preview_path = os.path.join(temp_dir, "preview.png")
                self._create_preview_for_export(temp_dir, preview_path)
                
                export_model_zip(model_data, temp_dir, zip_path)
            
            messagebox.showinfo("Экспортировано", f"Модель экспортирована:\n{zip_path}")
            logger.info(f"Model exported to: {zip_path}")
            
        except Exception as e:
            import traceback
            tb = traceback.format_exc()
            with open("export_zip_error.log", "w", encoding="utf-8") as f:
                f.write(tb)
                
            messagebox.showerror("Ошибка экспорта", f"Ошибка при экспорте: {e}. Смотри export_zip_error.log")
            logger.error(f"Error exporting model: {e}\n{tb}")
    
    def _create_preview_for_export(self, temp_dir, preview_path):
        """Создает превью модели для экспорта"""
        try:
            # Для экспорта используем полные изображения
            base = Image.new("RGBA", (self.canvas_width, self.canvas_height), (0, 0, 0, 0))
            center_x = self.canvas_width // 2
            center_y = self.canvas_height // 2
            
            visible_items = [ci for ci in self.items if ci.visible]
            
            for ci in visible_items:
                # Загружаем полное изображение для экспорта
                try:
                    img = Image.open(ci.image_path).convert("RGBA")
                    
                    # Применяем трансформации
                    if ci.scale != 1.0:
                        new_width = max(1, int(img.width * ci.scale))
                        new_height = max(1, int(img.height * ci.scale))
                        img = img.resize((new_width, new_height), Image.LANCZOS)
                    
                    if ci.rotation != 0:
                        img = img.rotate(ci.rotation, expand=True, resample=Image.BICUBIC)
                    
                    if ci.flip_horizontal:
                        img = img.transpose(Image.FLIP_LEFT_RIGHT)
                    if ci.flip_vertical:
                        img = img.transpose(Image.FLIP_TOP_BOTTOM)
                    
                    px = center_x - img.size[0] // 2 + int(ci.x)
                    py = center_y - img.size[1] // 2 + int(ci.y)
                    
                    base.alpha_composite(img, (px, py))
                except Exception as e:
                    logger.error(f"Error creating preview for export: {e}")
                    
            base.thumbnail((200, 200))
            base.save(preview_path)
        except Exception as e:
            logger.error(f"Error creating preview for export: {e}")
    
    def import_zip(self):
        """Импорт модели из ZIP архива"""
        try:
            from utils import import_model_zip
            
            zip_path = filedialog.askopenfilename(
                title="Выберите ZIP архив с моделью",
                filetypes=[("ZIP архивы", "*.zip"), ("Все файлы", "*.*")]
            )
            
            if not zip_path:
                return
            
            try:
                model_data, import_dir = import_model_zip(zip_path)
                
                self.model = model_data
                self.model_dir = import_dir
                self.original_slot = None
                
                self.model_name_var.set(self.model.get("name", "Без названия"))
                
                self.canvas_width = self.model.get("width", 700)
                self.canvas_height = self.model.get("height", 700)
                self.canvas_width_var.set(self.canvas_width)
                self.canvas_height_var.set(self.canvas_height)
                
                self.items.clear()
                for layer in self.model.get("layers", []):
                    filename = layer.get("file")
                    if not filename:
                        continue
                        
                    file_path = os.path.join(import_dir, filename)
                    if not os.path.exists(file_path):
                        found = False
                        for root, dirs, files in os.walk(import_dir):
                            if filename in files:
                                file_path = os.path.join(root, filename)
                                found = True
                                break
                        
                        if not found:
                            logger.warning(f"File not found: {filename}")
                            continue
                    
                    try:
                        ci = CanvasItem(layer, file_path)
                        self.items.append(ci)
                    except Exception as e:
                        logger.error(f"Error loading image: {e}")
                
                self.imported_files.clear()
                for layer in self.model.get("layers", []):
                    filename = layer.get("file")
                    if filename:
                        file_path = os.path.join(import_dir, filename)
                        if os.path.exists(file_path):
                            try:
                                with Image.open(file_path) as img:
                                    is_gif = img.format == "GIF" and img.is_animated
                                    img.seek(0)
                                    preview_img = img.copy().convert("RGBA")
                                    preview_img.thumbnail((50, 50), Image.LANCZOS)
                                self.imported_files.append((filename, preview_img, is_gif))
                            except Exception as e:
                                logger.error(f"Error loading imported file: {e}")
                
                self.refresh_import_list()
                
                self.tree_state["expanded_groups"].clear()
                self.tree_state["selected_items"].clear()
                self.tree_state["preserve_selection"] = False
                
                self.refresh_tree()
                self.zoom_reset()
                self._canvas_cache_valid = False
                self.redraw_canvas()
                
                self.cleanup_old_temp_folders()
                
                messagebox.showinfo("Импорт завершен", f"Модель успешно импортирована из:\n{os.path.basename(zip_path)}")
                logger.info(f"Model imported from ZIP: {zip_path}")
                
            except Exception as e:
                import traceback
                tb = traceback.format_exc()
                messagebox.showerror("Ошибка импорта", f"Ошибка при импорте модели: {e}")
                logger.error(f"Error importing model from ZIP: {e}\n{tb}")
                
        except Exception as e:
            messagebox.showerror("Ошибка импорта", f"Не удалось импортировать утилиту импорта: {e}")
            logger.error(f"Error importing import utility: {e}")
            return

    def on_window_resize(self, event):
        """Обработка изменения размера окна - перерисовываем холст"""
        self._canvas_cache_valid = False
        self.redraw_canvas()

    def update_model_name(self, event=None):
        """Обновление имени модели"""
        self.model["name"] = self.model_name_var.get()
        logger.info(f"Model name updated to: {self.model['name']}")
    
    def redraw_canvas(self, level=0.0, mode="none"):
        """Оптимизированная перерисовка холста"""
        if self._redraw_scheduled:
            return
            
        self._redraw_scheduled = True
        
        def do_redraw():
            self._redraw_scheduled = False
            if not self.winfo_exists():
                return
                
            try:
                # Быстрая очистка
                self.canvas.delete("all")
                
                # Простой рендеринг без сложных вычислений
                self._fast_render_canvas(level, mode)
            except Exception as e:
                logger.error(f"Redraw error: {e}")
        
        self.after(self._redraw_delay, do_redraw)
    
    def _fast_render_canvas(self, level=0.0, mode="none"):
        """Быстрый рендеринг холста"""
        canvas_width = self.canvas.winfo_width()
        canvas_height = self.canvas.winfo_height()
        
        if canvas_width < 10 or canvas_height < 10:
            return
        
        # Рассчитываем размеры с учетом зума
        scaled_width = int(self.canvas_width * self.zoom_level)
        scaled_height = int(self.canvas_height * self.zoom_level)
        
        center_x = canvas_width // 2 + self.offset_x
        center_y = canvas_height // 2 + self.offset_y
        
        canvas_x1 = center_x - scaled_width // 2
        canvas_y1 = center_y - scaled_height // 2
        
        # Рисуем фон холста
        self.canvas.create_rectangle(
            canvas_x1, canvas_y1, 
            canvas_x1 + scaled_width, canvas_y1 + scaled_height,
            outline="#666", width=2, fill="#333"
        )
        
        # Определяем список элементов для отображения
        items_to_draw = []
        
        if mode == "none":
            # Режим редактирования - все видимые слои
            items_to_draw = [ci for ci in self.items if ci.visible]
        else:
            # Режим тестирования - используем логику групп
            current_state = "silent"
            if level > self.thresholds['shout']:
                current_state = "shout"
            elif level > self.thresholds['normal']:
                current_state = "normal"
            elif level > self.thresholds['whisper']:
                current_state = "whisper"
            elif level > self.thresholds['silent']:
                current_state = "silent"
            
            # Получаем видимые элементы для текущего состояния
            items_to_draw = self._get_visible_items_for_state(current_state)
        
        # Сортируем элементы для правильного наложения (по порядку в self.items)
        items_to_draw.sort(key=lambda x: self.items.index(x))
        
        # Отрисовываем все элементы
        for ci in items_to_draw:
            img = ci.get_image_for_display(self.zoom_level)
            if img is None:
                continue
            
            # Рассчитываем позицию на холсте
            img_center_x = scaled_width // 2
            img_center_y = scaled_height // 2
            
            pos_x = canvas_x1 + img_center_x - img.width // 2 + int(ci.x * self.zoom_level)
            pos_y = canvas_x1 + img_center_y - img.height // 2 + int(ci.y * self.zoom_level)
            
            # Используем кэшированные PhotoImage, но для GIF не кэшируем
            if ci.is_gif:
                # Для GIF создаем новый PhotoImage каждый раз
                photo = ImageTk.PhotoImage(img)
                # Сохраняем ссылку, чтобы не удалилась сборщиком мусора
                if not hasattr(ci, '_tk_images'):
                    ci._tk_images = []
                ci._tk_images.append(photo)
                # Ограничиваем количество сохраненных изображений
                if len(ci._tk_images) > 5:
                    ci._tk_images.pop(0)
            else:
                # Для статических изображений используем кэш
                photo_key = f"{id(img)}_{self.zoom_level}"
                if photo_key not in self._photo_images:
                    photo = ImageTk.PhotoImage(img)
                    self._photo_images[photo_key] = photo
                    
                    # Ограничиваем размер кэша
                    if len(self._photo_images) > self._photo_cache_limit:
                        oldest = next(iter(self._photo_images))
                        del self._photo_images[oldest]
                
                photo = self._photo_images[photo_key]
            
            self.canvas.create_image(pos_x, pos_y, image=photo, anchor="nw")
            
            # Выделение выбранных элементов (только в режиме редактирования)
            if mode == "none" and ci.layer.get("_selected"):
                self.canvas.create_rectangle(
                    pos_x, pos_y, pos_x + img.width, pos_y + img.height,
                    outline="cyan", width=2
                )

    def cleanup_old_temp_folders(self):
        """Очищает старые временные папки"""
        import re
        import time as tm
        
        all_folders = [f for f in os.listdir(MODELS_DIR) 
                      if os.path.isdir(os.path.join(MODELS_DIR, f))]
        
        temp_pattern = re.compile(r'^temp_(\d+)_slot(\d+)$')
        model_temp_pattern = re.compile(r'^model_temp_(\d+)$')
        
        now = tm.time()
        max_age = 3600  # 1 час
        
        for folder in all_folders:
            folder_path = os.path.join(MODELS_DIR, folder)
            try:
                if temp_pattern.match(folder) or model_temp_pattern.match(folder):
                    # Проверяем время создания
                    stat = os.stat(folder_path)
                    if now - stat.st_mtime > max_age:
                        shutil.rmtree(folder_path)
                        logger.info(f"Removed old temp folder: {folder}")
            except Exception as e:
                logger.error(f"Error checking temp folder {folder}: {e}")
    
    def group_selected(self):
        """Создает группу из выбранных элементов"""
        if not self.current_selection:
            messagebox.showwarning("Группа", "Выберите хотя бы один элемент")
            return
        
        name = simpledialog.askstring("Имя группы", "Введите имя новой группы", parent=self)
        if not name:
            return
        
        existing = [g.get("name") for g in self.model.get("groups", [])]
        if name in existing:
            messagebox.showwarning("Группа", "Имя группы уже существует")
            return
        
        # Определяем родительскую группу (если все выбранные элементы из одной группы)
        parent_group = None
        selected_groups = set()
        for ci in self.current_selection:
            group_name = ci.layer.get("group")
            if group_name:
                selected_groups.add(group_name)
                
        if len(selected_groups) == 1:
            parent_group = list(selected_groups)[0]
        elif self.selected_group:
            # Если выбрана группа в интерфейсе, используем ее как родительскую
            parent_group = self.selected_group
        
        new_group = {
            "name": name,
            "children": [ci.layer.get("name") for ci in self.current_selection],
            "parent": parent_group,
            "logic": {},
            "blink_freq": 0.0,
            "random_effect": False,
            "random_min": 5.0,
            "random_max": 10.0
        }
        
        self.model.setdefault("groups", []).append(new_group)
        
        for ci in self.current_selection:
            ci.layer["group"] = name
        
        # Обновляем родительскую группу (если есть)
        if parent_group:
            for g in self.model.get("groups", []):
                if g.get("name") == parent_group:
                    # Удаляем элементы из родительской группы
                    for ci in self.current_selection:
                        if ci.layer.get("name") in g.get("children", []):
                            g["children"].remove(ci.layer.get("name"))
                    # Добавляем новую группу как дочерний элемент
                    g["children"].append(name)
                    break
        
        self._save_tree_state()
        self.tree_state["preserve_selection"] = True
        
        for ci in self.items:
            ci.layer["_selected"] = False
        self.current_selection = []
        self.selected_group = name
        self.refresh_tree()
        self._canvas_cache_valid = False
        self.redraw_canvas()
        
        logger.info(f"Created new group: {name} with parent {parent_group}")
        self.save_to_history("Создание группы")
    
    def _save_tree_state(self):
        """Сохраняет состояние дерева"""
        self.tree_state["expanded_groups"].clear()
        self.tree_state["selected_items"].clear()
        
        for item in self.tree.get_children():
            if self.tree.item(item, "open"):
                values = self.tree.item(item, "values")
                if values and values[0] == "group":
                    self.tree_state["expanded_groups"].add(values[1])
        
        for item in self.tree.selection():
            values = self.tree.item(item, "values")
            if values:
                self.tree_state["selected_items"].add(tuple(values))
    
    def _restore_tree_state(self):
        """Восстанавливает состояние дерева"""
        for item in self.tree.get_children():
            values = self.tree.item(item, "values")
            if values and values[0] == "group" and values[1] in self.tree_state["expanded_groups"]:
                self.tree.item(item, open=True)
        
        if self.tree_state["preserve_selection"]:
            for item in self.tree.get_children():
                values = self.tree.item(item, "values")
                if values and tuple(values) in self.tree_state["selected_items"]:
                    self.tree.selection_add(item)
            
            self.tree_state["preserve_selection"] = False
    
    def refresh_tree(self):
        """Обновляет дерево элементов"""
        try:
            # Сохраняем текущее выделение
            selected_ids = []
            for ci in self.current_selection:
                selected_ids.append(id(ci))
            
            self._save_tree_state()
            
            self.tree.delete(*self.tree.get_children())
            
            groups_by_name = {g.get("name"): g for g in self.model.get("groups", [])}
            group_nodes = {}
            items_added = set()
            
            # Функция для получения пути группы (от корня до текущей группы)
            def get_group_path(group_name):
                path = []
                current = group_name
                while current:
                    path.insert(0, current)
                    group = groups_by_name.get(current)
                    current = group.get("parent") if group else None
                return path
            
            # Создаем узлы для всех групп (если они еще не созданы)
            def ensure_group_node(group_name):
                if group_name in group_nodes:
                    return group_nodes[group_name]
                    
                group = groups_by_name.get(group_name)
                if not group:
                    return None
                    
                # Получаем путь группы
                path = get_group_path(group_name)
                
                # Создаем узлы для всех групп в пути
                parent_node = ""
                for group_in_path in path:
                    if group_in_path in group_nodes:
                        parent_node = group_nodes[group_in_path]
                        continue
                        
                    # Создаем узел для этой группы
                    group_node = self.tree.insert(parent_node, "end", text=f"📁 {group_in_path}",
                                                 values=("group", group_in_path))
                    group_nodes[group_in_path] = group_node
                    parent_node = group_node
                    
                return group_nodes[group_name]
            
            # Проходим по элементам в ОБРАТНОМ порядке
            for ci in reversed(self.items):
                item_id = id(ci)
                if item_id in items_added:
                    continue
                    
                group_name = ci.layer.get("group")
                
                if group_name:
                    # Убеждаемся, что узел группы существует
                    group_node = ensure_group_node(group_name)
                    if group_node:
                        # Добавляем элемент в группу
                        self.tree.insert(group_node, "end", 
                                        text=self._get_item_display_text(ci),
                                        values=("item", item_id))
                        items_added.add(item_id)
                    else:
                        # Группа не найдена, добавляем в корень
                        self.tree.insert("", "end", text=self._get_item_display_text(ci),
                                        values=("item", item_id))
                        items_added.add(item_id)
                else:
                    # Элемент без группы - добавляем в корень
                    self.tree.insert("", "end", text=self._get_item_display_text(ci),
                                    values=("item", item_id))
                    items_added.add(item_id)
            
            self._restore_tree_state()
            
            # Восстанавливаем выделение
            self.current_selection = []
            for ci in self.items:
                if id(ci) in selected_ids:
                    ci.layer["_selected"] = True
                    self.current_selection.append(ci)
                    # Выделяем в дереве
                    self._select_item_in_tree(ci)
            
        except Exception as e:
            logger.error(f"Error refreshing tree: {e}")
    
    def _get_item_display_text(self, ci):
        """Генерирует текст для отображения элемента в дереве"""
        layer = ci.layer
        name = layer.get("name", "unnamed")
        flags = []
        
        if not ci.visible:
            flags.append("✘")
        else:
            flags.append("✔")
        
        if ci.is_gif:
            flags.append("GIF")
        
        if ci.flip_horizontal:
            flags.append("зерк.гор")
        
        if ci.flip_vertical:
            flags.append("зерк.верт")
        
        if ci.rotation != 0:
            flags.append(f"↻{ci.rotation}°")
        
        if ci.scale != 1.0:
            flags.append(f"⤢{ci.scale}")
        
        flag_text = f" ({', '.join(flags)})" if flags else ""
        return f"{name}{flag_text}"
    
    def _get_item_by_id(self, item_id):
        """Находит элемент по ID"""
        for ci in self.items:
            if id(ci) == item_id:
                return ci
        return None
    
    def _select_item_in_tree(self, canvas_item):
        """Выделяет элемент в дереве по CanvasItem"""
        # Находим ID элемента в дереве
        for item_id in self.tree.get_children():
            values = self.tree.item(item_id, "values")
            if values and values[0] == "item":
                item_id_int = int(values[1])
                if item_id_int == id(canvas_item):
                    self.tree.selection_set(item_id)
                    self.tree.focus(item_id)
                    # Прокручиваем к выбранному элементу
                    self.tree.see(item_id)
                    return
        
        # Проверяем дочерние элементы (для элементов в группах)
        for parent_id in self.tree.get_children():
            for child_id in self.tree.get_children(parent_id):
                values = self.tree.item(child_id, "values")
                if values and values[0] == "item":
                    item_id_int = int(values[1])
                    if item_id_int == id(canvas_item):
                        self.tree.selection_set(child_id)
                        self.tree.focus(child_id)
                        # Раскрываем родительскую группу
                        self.tree.item(parent_id, open=True)
                        # Прокручиваем к выбранному элементу
                        self.tree.see(child_id)
                        return
    
    def on_tree_select(self, event=None):
        """Обработчик выбора в дереве"""
        try:
            focus_widget = self.focus_get()
            if isinstance(focus_widget, (ttk.Entry, tk.Entry, ttk.Combobox)):
                return
            
            selection = self.tree.selection()
            self.current_selection = []
            self.selected_group = None
            
            for c in self.items:
                c.layer["_selected"] = False
            
            if not selection:
                self.group_label.config(text="(нет группы)")
                self.clear_props_fields()
                self._canvas_cache_valid = False
                self.redraw_canvas()
                return
            
            selected_groups = set()
            selected_items = set()
            
            for item_id in selection:
                item_values = self.tree.item(item_id, "values")
                if not item_values:
                    continue
                
                if item_values[0] == "group":
                    group_name = item_values[1]
                    selected_groups.add(group_name)
                    
                    # Выделяем все элементы группы
                    for ci in self.items:
                        if ci.layer.get("group") == group_name:
                            ci.layer["_selected"] = True
                            selected_items.add(ci)
                elif item_values[0] == "item":
                    item_id_int = int(item_values[1])
                    ci = self._get_item_by_id(item_id_int)
                    if ci:
                        ci.layer["_selected"] = True
                        selected_items.add(ci)
            
            self.current_selection = list(selected_items)
            
            if len(selected_groups) == 1:
                self.selected_group = list(selected_groups)[0]
                self.group_label.config(text=self.selected_group)
                self.load_group_settings(self.selected_group)
            else:
                self.selected_group = None
                self.group_label.config(text="(нет группы)")
                if self.current_selection:
                    self.load_item_props(self.current_selection[0])
                else:
                    self.clear_props_fields()
            
            self._canvas_cache_valid = False
            self.redraw_canvas()
            
        except Exception as e:
            logger.error(f"Error in on_tree_select: {e}")
    
    def bring_forward(self):
        """Перемещает выделенные элементы вперед"""
        if not self.current_selection:
            return
        
        # Сортируем по текущей позиции
        sorted_items = sorted(self.current_selection, key=lambda x: self.items.index(x), reverse=True)
        
        for ci in sorted_items:
            idx = self.items.index(ci)
            if idx < len(self.items) - 1:
                # Меняем местами со следующим элементом
                self.items[idx], self.items[idx + 1] = self.items[idx + 1], self.items[idx]
        
        self._save_tree_state()
        self.tree_state["preserve_selection"] = True
        
        self.refresh_tree()
        self._canvas_cache_valid = False
        self.redraw_canvas()
        
        logger.info("Brought selection forward")
        self.save_to_history("Перемещение вперед")
    
    def send_backward(self):
        """Перемещает выделенные элементы назад"""
        if not self.current_selection:
            return
        
        # Сортируем по текущей позиции
        sorted_items = sorted(self.current_selection, key=lambda x: self.items.index(x))
        
        for ci in sorted_items:
            idx = self.items.index(ci)
            if idx > 0:
                # Меняем местами с предыдущим элементом
                self.items[idx], self.items[idx - 1] = self.items[idx - 1], self.items[idx]
        
        self._save_tree_state()
        self.tree_state["preserve_selection"] = True
        
        self.refresh_tree()
        self._canvas_cache_valid = False
        self.redraw_canvas()
        
        logger.info("Sent selection backward")
        self.save_to_history("Перемещение назад")
    
    def apply_group_logic(self):
        """Применяет логику группы"""
        if not self.selected_group:
            messagebox.showwarning("Нет группы", "Сначала выберите группу")
            return
        
        group_name = self.selected_group
        group = None
        
        for g in self.model.get("groups", []):
            if g.get("name") == group_name:
                group = g
                break
        
        if not group:
            messagebox.showerror("Ошибка", "Группа не найдена")
            return
        
        # Сохраняем логику состояний
        logic = {}
        for state, var in self.state_vars.items():
            val = var.get().strip()
            if val:
                logic[state] = val
        
        group["logic"] = logic
        
        # Сохраняем настройки моргания
        try:
            blink_freq = self.blink_freq.get()
            if blink_freq == "":
                blink_freq = 0.0
            group["blink_freq"] = float(blink_freq)
        except Exception as e:
            logger.error(f"Error converting blink_freq: {e}")
            group["blink_freq"] = 0.0
        
        # Сохраняем настройки случайного эффекта
        group["random_effect"] = self.random_effect_var.get()
        group["random_min"] = self.random_min_var.get()
        group["random_max"] = self.random_max_var.get()
        
        self._save_tree_state()
        self.tree_state["preserve_selection"] = True
        
        messagebox.showinfo("Логика группы", f"Логика для группы {group_name} сохранена")
        logger.info(f"Group logic applied to {group_name}")
    
    def on_mirror_change(self):
        """Обработчик изменения отражения"""
        if not self.current_selection:
            return
        
        for ci in self.current_selection:
            ci.flip_horizontal = self.flip_h_var.get()
            ci.flip_vertical = self.flip_v_var.get()
            ci.clear_cache()  # Очищаем кэш
        
        self._canvas_cache_valid = False
        self.redraw_canvas()
        self.save_to_history("Изменение отражения")
    
    def on_visible_change(self):
        """Обработчик изменения видимости"""
        if not self.current_selection:
            return
        
        for ci in self.current_selection:
            ci.visible = self.visible_var.get()
            ci.clear_cache()  # Очищаем кэш
        
        self._canvas_cache_valid = False
        self.redraw_canvas()
        self.save_to_history("Изменение видимости")
    
    def zoom_in(self):
        """Увеличивает масштаб"""
        self.zoom_level = min(self.max_zoom, self.zoom_level + self.zoom_step)
        self._canvas_cache_valid = False
        self.redraw_canvas()
    
    def zoom_out(self):
        """Уменьшает масштаб"""
        self.zoom_level = max(self.min_zoom, self.zoom_level - self.zoom_step)
        self._canvas_cache_valid = False
        self.redraw_canvas()
    
    def zoom_reset(self):
        """Сбрасывает масштаб"""
        self.zoom_level = 1.0
        self.offset_x = 0
        self.offset_y = 0
        self._canvas_cache_valid = False
        self.redraw_canvas()
    
    def on_canvas_zoom(self, event):
        """Обработчик зума колесиком мыши"""
        if event.delta > 0 or event.num == 4:
            self.zoom_in()
        else:
            self.zoom_out()
    
    def on_canvas_pan_start(self, event):
        """Начало панорамирования"""
        self.is_panning = True
        self.last_pan_x = event.x
        self.last_pan_y = event.y
        self.canvas.config(cursor="fleur")
    
    def on_canvas_pan_move(self, event):
        """Панорамирование"""
        if self.is_panning:
            dx = event.x - self.last_pan_x
            dy = event.y - self.last_pan_y
            self.offset_x += dx
            self.offset_y += dy
            self.last_pan_x = event.x
            self.last_pan_y = event.y
            self._canvas_cache_valid = False
            self.redraw_canvas()
    
    def on_canvas_pan_end(self, event):
        """Конец панорамирования"""
        self.is_panning = False
        self.canvas.config(cursor="crosshair")
    
    def update_canvas_size(self, event=None):
        """Обновляет размер холста"""
        try:
            new_width = max(100, min(1500, self.canvas_width_var.get()))
            new_height = max(100, min(1500, self.canvas_height_var.get()))
            self.canvas_width = new_width
            self.canvas_height = new_height
            self.canvas_width_var.set(new_width)
            self.canvas_height_var.set(new_height)
            self.model["width"] = new_width
            self.model["height"] = new_height
            self.zoom_reset()
            self._canvas_cache_valid = False
            logger.info(f"Canvas size updated to: {new_width}x{new_height}")
        except Exception as e:
            messagebox.showerror("Ошибка", f"Неверный размер холста: {e}")
            logger.error(f"Error updating canvas size: {e}")
    
    def on_close(self):
        """Закрытие редактора"""
        try:
            self.audio_processor.stop()
            self.stop_blink_preview()
        except Exception as e:
            logger.error(f"Error stopping audio: {e}")
        
        # Удаляем временную папку
        if self.model_dir and "temp_" in self.model_dir and os.path.exists(self.model_dir):
            try:
                shutil.rmtree(self.model_dir)
                logger.info(f"Temporary directory removed: {self.model_dir}")
            except Exception as e:
                logger.error(f"Error removing temporary directory: {e}")
        
        self.grab_release()
        logger.info("Model editor closed")
        self.destroy()
    
    def update_test_mode(self):
        """Обновляет режим тестирования"""
        mode = self.test_mode_var.get()
        
        if mode == "none":
            self.level_frame.pack_forget()
            self.audio_processor.stop()
            self.audio_level = 0.0
            self.level_bar["value"] = 0
        else:
            self.level_frame.pack(fill="x", pady=5)
            
            if mode == "microphone":
                try:
                    self.audio_processor.stop()
                    self.audio_processor = AudioProcessor(
                        callback=self.on_audio_level,
                        device=self.mic_device
                    )
                    self.audio_processor.noise_gate_threshold = self.mic_noise_gate_threshold
                    self.audio_processor.set_sensitivity(self.mic_sensitivity)
                    self.audio_processor.start()
                except Exception as e:
                    logger.error(f"Error starting audio processor: {e}")
    
    def on_audio_level(self, level):
        """Обработчик уровня аудио"""
        level_scaled = level * self.mic_sensitivity
        if self.test_mode_var.get() == "microphone":
            self.level_bar["value"] = level_scaled * 100
        self.audio_level = level_scaled
    
    def new_model(self):
        """Создает новую модель"""
        name = simpledialog.askstring("Имя модели", "Введите имя модели", parent=self)
        if not name:
            return
        
        self.model = {"name": name, "layers": [], "groups": [], "width": 700, "height": 700}
        self.model_dir = None
        self.original_slot = None
        self.items.clear()
        self.imported_files.clear()
        self.model_name_var.set(name)
        self.canvas_width = 700
        self.canvas_height = 700
        self.canvas_width_var.set(700)
        self.canvas_height_var.set(700)
        self.refresh_import_list()
        
        self.tree_state["expanded_groups"].clear()
        self.tree_state["selected_items"].clear()
        self.tree_state["preserve_selection"] = False
        
        self.refresh_tree()
        self.zoom_reset()
        self._canvas_cache_valid = False
        self.redraw_canvas()
        
        logger.info(f"New model created: {name}")
    
    def load_model(self):
        """Загружает модель из слота"""
        slot_dialog = tk.Toplevel(self)
        slot_dialog.title("Загрузка из слота")
        slot_dialog.geometry("400x500")
        slot_dialog.transient(self)
        slot_dialog.grab_set()
        slot_dialog.resizable(False, False)
        
        slot_dialog.images = []
        
        ttk.Label(slot_dialog, text="Выберите слот для загрузки:", 
                font=("Arial", 10, "bold")).pack(pady=10)
        
        main_frame = ttk.Frame(slot_dialog)
        main_frame.pack(fill="both", expand=True, padx=10, pady=5)
        
        canvas = tk.Canvas(main_frame)
        scrollbar = ttk.Scrollbar(main_frame, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=scrollbar.set)
        
        scrollbar.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)
        
        inner_frame = ttk.Frame(canvas)
        canvas.create_window((0, 0), window=inner_frame, anchor="nw", width=380)
        
        def configure_scrollregion(event):
            canvas.configure(scrollregion=canvas.bbox("all"))
        
        inner_frame.bind("<Configure>", configure_scrollregion)
        
        # Превью текущей модели
        current_preview_frame = ttk.LabelFrame(inner_frame, text="Текущая модель")
        current_preview_frame.pack(fill="x", pady=(0, 10), padx=5)
        
        current_preview_label = ttk.Label(current_preview_frame)
        current_preview_label.pack(padx=5, pady=5)
        
        try:
            if self.model_dir:
                preview_path = os.path.join(self.model_dir, "preview.png")
                if os.path.exists(preview_path):
                    img = Image.open(preview_path)
                    img.thumbnail((150, 150), Image.LANCZOS)
                    current_preview_img = ImageTk.PhotoImage(img)
                    slot_dialog.images.append(current_preview_img)
                    current_preview_label.configure(image=current_preview_img)
                else:
                    img = Image.new("RGBA", (150, 150), (0, 0, 0, 0))
                    current_preview_img = ImageTk.PhotoImage(img)
                    slot_dialog.images.append(current_preview_img)
                    current_preview_label.configure(image=current_preview_img)
        except Exception as e:
            logger.error(f"Error loading current preview: {e}")
        
        # Слоты
        slots_frame = ttk.Frame(inner_frame)
        slots_frame.pack(fill="both", expand=True, padx=5, pady=5)
        
        for i in range(1, 7):
            slot_frame = ttk.LabelFrame(slots_frame, text=f"Слот {i}")
            slot_frame.pack(fill="x", pady=5, padx=5)
            
            content_frame = ttk.Frame(slot_frame)
            content_frame.pack(fill="x", padx=5, pady=5)
            
            # Превью слота
            try:
                preview_path = os.path.join(MODELS_DIR, f"slot{i}", "preview.png")
                if os.path.exists(preview_path):
                    img = Image.open(preview_path)
                    img.thumbnail((80, 80), Image.LANCZOS)
                    preview_img = ImageTk.PhotoImage(img)
                    slot_dialog.images.append(preview_img)
                    
                    preview_label = ttk.Label(content_frame, image=preview_img)
                    preview_label.pack(side="left", padx=5, pady=5)
                else:
                    img = Image.new("RGBA", (80, 80), (0, 0, 0, 0))
                    preview_img = ImageTk.PhotoImage(img)
                    slot_dialog.images.append(preview_img)
                    
                    preview_label = ttk.Label(content_frame, image=preview_img)
                    preview_label.pack(side="left", padx=5, pady=5)
            except Exception as e:
                logger.error(f"Error loading preview for slot {i}: {e}")
            
            # Информация о слоте
            info_frame = ttk.Frame(content_frame)
            info_frame.pack(side="left", fill="both", expand=True, padx=5)
            
            slot_dir = os.path.join(MODELS_DIR, f"slot{i}")
            json_path = os.path.join(slot_dir, "model.json")
            
            if os.path.exists(json_path):
                try:
                    with open(json_path, "r", encoding="utf-8") as f:
                        model_data = json.load(f)
                    model_name = model_data.get('name', f'Слот {i}')
                    status_text = model_name
                except:
                    status_text = "Ошибка загрузки"
            else:
                status_text = "Пустой слот"
            
            status = ttk.Label(info_frame, text=status_text, font=("Arial", 9))
            status.pack(anchor="w", pady=(5, 0))
            
            # Кнопка загрузки
            btn = ttk.Button(
                info_frame, 
                text="Загрузить",
                command=lambda slot=i, dlg=slot_dialog: self._load_slot(slot, dlg)
            )
            btn.pack(anchor="w", pady=5)
        
        ttk.Button(slot_dialog, text="Отмена", command=slot_dialog.destroy).pack(pady=10)
        
        slot_dialog.update()
    
    def _load_slot(self, slot_num, dialog):
        """Загружает модель из слота"""
        dialog.destroy()
        path = os.path.join(MODELS_DIR, f"slot{slot_num}")
        json_path = os.path.join(path, "model.json")
        
        if not os.path.exists(json_path):
            messagebox.showerror("Ошибка", "model.json не найден в выбранном слоте")
            return
        
        with open(json_path, "r", encoding="utf-8") as f:
            self.model = json.load(f)
        
        self.model_name_var.set(self.model.get("name", "Без названия"))
        
        self.canvas_width = self.model.get("width", 700)
        self.canvas_height = self.model.get("height", 700)
        self.canvas_width_var.set(self.canvas_width)
        self.canvas_height_var.set(self.canvas_height)
        
        # Создаем временную папку
        temp_dir = os.path.join(MODELS_DIR, f"temp_{int(time.time())}_slot{slot_num}")
        os.makedirs(temp_dir, exist_ok=True)
        
        # Копируем файлы
        for f in os.listdir(path):
            src = os.path.join(path, f)
            if os.path.isfile(src):
                shutil.copy2(src, os.path.join(temp_dir, f))
        
        self.model_dir = temp_dir
        self.original_slot = slot_num
        
        # Загружаем элементы
        self.items.clear()
        for layer in self.model.get("layers", []):
            filename = layer.get("file")
            if not filename:
                continue
            
            file_path = os.path.join(self.model_dir, filename)
            if not os.path.exists(file_path):
                continue
            
            try:
                ci = CanvasItem(layer, file_path)
                self.items.append(ci)
            except Exception as e:
                logger.error(f"Error loading image: {e}")
        
        # Загружаем список импортированных файлов
        self.imported_files.clear()
        for f in os.listdir(self.model_dir):
            if f.lower().endswith((".png", ".gif")):
                try:
                    fp = os.path.join(self.model_dir, f)
                    with Image.open(fp) as img:
                        is_gif = img.format == "GIF" and img.is_animated
                        img.seek(0)
                        preview_img = img.copy().convert("RGBA")
                        preview_img.thumbnail((50, 50), Image.LANCZOS)
                    self.imported_files.append((f, preview_img, is_gif))
                except Exception as e:
                    logger.error(f"Error loading imported file: {e}")
        
        self.refresh_import_list()
        
        self.tree_state["expanded_groups"].clear()
        self.tree_state["selected_items"].clear()
        self.tree_state["preserve_selection"] = False
        
        self.refresh_tree()
        self.zoom_reset()
        self._canvas_cache_valid = False
        self.redraw_canvas()
        
        self.cleanup_old_temp_folders()
        
        logger.info(f"Model loaded from slot {slot_num}")
    
    def save_model(self):
        """Сохраняет модель"""
        if not self.model_dir:
            tmp = os.path.join(MODELS_DIR, f"model_temp_{int(time.time())}")
            os.makedirs(tmp, exist_ok=True)
            self.model_dir = tmp
            
            self.cleanup_old_temp_folders()
        
        self.model["name"] = self.model_name_var.get()
        self.model["width"] = self.canvas_width
        self.model["height"] = self.canvas_height
        
        # Обновляем данные слоев
        self.model["layers"] = []
        for ci in self.items:
            layer = ci.layer.copy() if ci.layer else {}
            layer["x"] = int(ci.x)
            layer["y"] = int(ci.y)
            layer["visible"] = bool(ci.visible)
            layer["is_gif"] = ci.is_gif
            layer["scale"] = float(ci.scale)
            layer["rotation"] = int(ci.rotation)
            layer["flip_horizontal"] = bool(ci.flip_horizontal)
            layer["flip_vertical"] = bool(ci.flip_vertical)
            
            if "file" in layer:
                layer["file"] = os.path.basename(layer["file"])
            
            if not layer.get("name") and ci.layer and ci.layer.get("name"):
                layer["name"] = ci.layer.get("name")
            
            self.model["layers"].append(layer)
        
        # Сохраняем JSON
        model_json_path = os.path.join(self.model_dir, "model.json")
        try:
            with open(model_json_path, "w", encoding="utf-8") as f:
                json.dump(self.model, f, indent=2, ensure_ascii=False)
        except Exception as e:
            logger.error(f"Error saving model JSON: {e}")
            messagebox.showerror("Ошибка", f"Не удалось сохранить модель: {e}")
            return
        
        # Создаем превью
        self.create_preview()
        
        # Показываем диалог выбора слота
        self.show_save_slot_dialog()
        
        self.last_autosave = time.time()
        logger.info("Model saved, showing slot selection dialog with previews")
    
    def show_save_slot_dialog(self):
        """Показывает диалог сохранения в слот"""
        slot_dialog = tk.Toplevel(self)
        slot_dialog.title("Сохранение в слот")
        slot_dialog.geometry("400x500")
        slot_dialog.transient(self)
        slot_dialog.grab_set()
        slot_dialog.resizable(False, False)
        
        slot_dialog.images = []
        
        ttk.Label(slot_dialog, text="Выберите слот для сохранения:", 
                font=("Arial", 10, "bold")).pack(pady=10)
        
        main_frame = ttk.Frame(slot_dialog)
        main_frame.pack(fill="both", expand=True, padx=10, pady=5)
        
        canvas = tk.Canvas(main_frame)
        scrollbar = ttk.Scrollbar(main_frame, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=scrollbar.set)
        
        scrollbar.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)
        
        inner_frame = ttk.Frame(canvas)
        canvas.create_window((0, 0), window=inner_frame, anchor="nw", width=380)
        
        def configure_scrollregion(event):
            canvas.configure(scrollregion=canvas.bbox("all"))
        
        inner_frame.bind("<Configure>", configure_scrollregion)
        
        # Превью текущей модели
        current_preview_frame = ttk.LabelFrame(inner_frame, text="Текущая модель для сохранения")
        current_preview_frame.pack(fill="x", pady=(0, 10), padx=5)
        
        current_preview_label = ttk.Label(current_preview_frame)
        current_preview_label.pack(padx=5, pady=5)
        
        try:
            if self.model_dir:
                preview_path = os.path.join(self.model_dir, "preview.png")
                if os.path.exists(preview_path):
                    img = Image.open(preview_path)
                    img.thumbnail((120, 120), Image.LANCZOS)
                    current_preview_img = ImageTk.PhotoImage(img)
                    slot_dialog.images.append(current_preview_img)
                    current_preview_label.configure(image=current_preview_img)
                else:
                    img = Image.new("RGBA", (120, 120), (0, 0, 0, 0))
                    current_preview_img = ImageTk.PhotoImage(img)
                    slot_dialog.images.append(current_preview_img)
                    current_preview_label.configure(image=current_preview_img)
        except Exception as e:
            logger.error(f"Error loading current preview: {e}")
        
        ttk.Label(current_preview_frame, 
                text=f"Имя: {self.model_name_var.get()}", 
                font=("Arial", 9)).pack()
        
        # Слоты
        slots_frame = ttk.Frame(inner_frame)
        slots_frame.pack(fill="both", expand=True, padx=5, pady=5)
        
        for i in range(1, 7):
            slot_frame = ttk.LabelFrame(slots_frame, text=f"Слот {i}")
            slot_frame.pack(fill="x", pady=5, padx=5)
            
            content_frame = ttk.Frame(slot_frame)
            content_frame.pack(fill="x", padx=5, pady=5)
            
            # Превью слота
            try:
                preview_path = os.path.join(MODELS_DIR, f"slot{i}", "preview.png")
                if os.path.exists(preview_path):
                    img = Image.open(preview_path)
                    img.thumbnail((80, 80), Image.LANCZOS)
                    preview_img = ImageTk.PhotoImage(img)
                    slot_dialog.images.append(preview_img)
                    
                    preview_label = ttk.Label(content_frame, image=preview_img)
                    preview_label.pack(side="left", padx=5, pady=5)
                else:
                    img = Image.new("RGBA", (80, 80), (0, 0, 0, 0))
                    preview_img = ImageTk.PhotoImage(img)
                    slot_dialog.images.append(preview_img)
                    
                    preview_label = ttk.Label(content_frame, image=preview_img)
                    preview_label.pack(side="left", padx=5, pady=5)
            except Exception as e:
                logger.error(f"Error loading preview for slot {i}: {e}")
            
            # Информация о слоте
            info_frame = ttk.Frame(content_frame)
            info_frame.pack(side="left", fill="both", expand=True, padx=5)
            
            slot_dir = os.path.join(MODELS_DIR, f"slot{i}")
            json_path = os.path.join(slot_dir, "model.json")
            
            if os.path.exists(json_path):
                try:
                    with open(json_path, "r", encoding="utf-8") as f:
                        model_data = json.load(f)
                    model_name = model_data.get('name', f'Слот {i}')
                    status_text = f"Занят: {model_name}"
                    btn_text = "Перезаписать"
                except:
                    status_text = "Ошибка загрузки"
                    btn_text = "Перезаписать"
            else:
                status_text = "Пустой слот"
                btn_text = "Сохранить"
            
            status = ttk.Label(info_frame, text=status_text, font=("Arial", 8))
            status.pack(anchor="w", pady=(5, 0))
            
            btn = ttk.Button(
                info_frame, 
                text=btn_text,
                command=lambda slot=i, dlg=slot_dialog: self._save_slot(slot, dlg)
            )
            btn.pack(anchor="w", pady=5)
        
        ttk.Button(slot_dialog, text="Отмена", command=slot_dialog.destroy).pack(pady=10)
        
        slot_dialog.update()
    
    def _save_slot(self, slot_num, dialog):
        """Сохраняет модель в слот"""
        dialog.destroy()
        
        slot_dir = os.path.join(MODELS_DIR, f"slot{slot_num}")
        os.makedirs(slot_dir, exist_ok=True)
        
        try:
            # Очищаем слот
            for f in os.listdir(slot_dir):
                file_path = os.path.join(slot_dir, f)
                try:
                    if os.path.isfile(file_path):
                        os.unlink(file_path)
                except Exception as e:
                    logger.error(f"Error deleting file {file_path}: {e}")
            
            # Копируем файлы
            for f in os.listdir(self.model_dir):
                src = os.path.join(self.model_dir, f)
                if os.path.isfile(src):
                    shutil.copy2(src, os.path.join(slot_dir, f))
            
            self.original_slot = slot_num
            
            messagebox.showinfo("Сохранено", f"Модель сохранена в слот {slot_num}")
            
            if self.on_save:
                self.on_save(self.model, slot_dir, slot_num)
            
            if hasattr(self.master, 'app') and hasattr(self.master.app, 'refresh_slot_buttons'):
                self.master.app.refresh_slot_buttons()
                
            logger.info(f"Model saved to slot {slot_num}")
            
        except Exception as e:
            logger.error(f"Error saving to slot {slot_num}: {e}")
            messagebox.showerror("Ошибка", f"Не удалось сохранить модель в слот {slot_num}: {e}")
    
    def create_preview(self):
        """Создает превью модели"""
        if not self.model_dir:
            return
        
        try:
            base = Image.new("RGBA", (self.canvas_width, self.canvas_height), (0, 0, 0, 0))
            center_x = self.canvas_width // 2
            center_y = self.canvas_height // 2
            
            visible_items = [ci for ci in self.items if ci.visible]
            
            for ci in visible_items:
                # Загружаем оригинальное изображение (как в рендерере)
                try:
                    img = Image.open(ci.image_path).convert("RGBA")
                    
                    # Применяем масштаб ПЕРВЫМ (как в рендерере)
                    if ci.scale != 1.0:
                        new_width = max(1, int(img.width * ci.scale))
                        new_height = max(1, int(img.height * ci.scale))
                        img = img.resize((new_width, new_height), Image.LANCZOS)
                    
                    # Затем отражение
                    if ci.flip_horizontal:
                        img = img.transpose(Image.FLIP_LEFT_RIGHT)
                    if ci.flip_vertical:
                        img = img.transpose(Image.FLIP_TOP_BOTTOM)
                    
                    # Затем поворот
                    if ci.rotation != 0:
                        img = img.rotate(ci.rotation, expand=True, resample=Image.BICUBIC)
                    
                    # Рассчитываем позицию
                    px = center_x - img.size[0] // 2 + int(ci.x)
                    py = center_y - img.size[1] // 2 + int(ci.y)
                    
                    base.alpha_composite(img, (px, py))
                except Exception as e:
                    logger.error(f"Error creating preview: {e}")
            
            # Уменьшаем для превью
            base.thumbnail((200, 200), Image.LANCZOS)
            preview_path = os.path.join(self.model_dir, "preview.png")
            base.save(preview_path)
            
        except Exception as e:
            logger.error(f"Error creating preview: {e}")
    
    def import_images(self):
        """Импортирует изображения"""
        files = filedialog.askopenfilenames(
            title="Выберите PNG или GIF изображения", 
            filetypes=[("Изображения", "*.png *.gif"), ("Все файлы", "*.*")]
        )
        if not files:
            return
        
        if not self.model_dir:
            tmp = os.path.join(MODELS_DIR, f"model_temp_{int(time.time())}")
            os.makedirs(tmp, exist_ok=True)
            self.model_dir = tmp
            
            self.cleanup_old_temp_folders()
        
        for file_path in files:
            try:
                base = os.path.basename(file_path)
                dest = os.path.join(self.model_dir, base)
                
                if os.path.abspath(file_path) != os.path.abspath(dest):
                    shutil.copy2(file_path, dest)
                
                # Определяем тип изображения
                is_gif = False
                if base.lower().endswith('.gif'):
                    with Image.open(file_path) as img:
                        is_gif = img.is_animated
                
                # Создаем превью для списка
                with Image.open(file_path) as img:
                    img.seek(0)
                    preview_img = img.copy().convert("RGBA")
                    preview_img.thumbnail((50, 50), Image.LANCZOS)
                
                self.imported_files.append((base, preview_img, is_gif))
                
                # Создаем слой
                layer = {
                    "name": os.path.splitext(base)[0], 
                    "file": base, 
                    "visible": True, 
                    "x": 0, 
                    "y": 0,
                    "scale": 1.0,
                    "rotation": 0,
                    "group": None,
                    "is_gif": is_gif,
                    "flip_horizontal": False,
                    "flip_vertical": False
                }
                self.model.setdefault("layers", []).append(layer)
                
                # Создаем элемент
                ci = CanvasItem(layer, dest)
                self.items.append(ci)
                
            except Exception as e:
                logger.error(f"Error importing image {file_path}: {e}")
        
        self.refresh_import_list()
        
        self._save_tree_state()
        self.tree_state["preserve_selection"] = True
        
        self.refresh_tree()
        self._canvas_cache_valid = False
        self.redraw_canvas()
        self.last_autosave = time.time()
        
        logger.info(f"Imported {len(files)} images")
        self.save_to_history("Импорт изображений")
    
    def refresh_import_list(self):
        """Обновляет список импортированных файлов"""
        for w in self.import_inner.winfo_children():
            w.destroy()
        
        for fname, img, is_gif in self.imported_files:
            row = ttk.Frame(self.import_inner)
            row.pack(fill="x", padx=2, pady=2)
            
            icon = "GIF" if is_gif else "PNG"
            ttk.Label(row, text=f"{icon}: {fname}", width=20).pack(side="left", padx=2)
            ttk.Button(row, text="+", width=2, command=lambda f=fname: self.add_to_canvas(f)).pack(side="left", padx=2)
            ttk.Button(row, text="-", width=2, command=lambda f=fname: self.remove_from_canvas_by_file(f)).pack(side="left", padx=2)
            ttk.Button(row, text="🗑️", width=2, command=lambda f=fname: self.delete_file(f)).pack(side="left", padx=2)
    
    def clear_props_fields(self):
        """Очищает поля свойств"""
        self.name_entry.delete(0, "end")
        self.x_entry.delete(0, "end")
        self.y_entry.delete(0, "end")
        self.scale_entry.delete(0, "end")
        self.rotation_entry.delete(0, "end")
        self.flip_h_var.set(False)
        self.flip_v_var.set(False)
        self.visible_var.set(True)
    
    def load_item_props(self, ci):
        """Загружает свойства элемента в поля"""
        try:
            self.name_entry.delete(0, "end")
            self.name_entry.insert(0, ci.layer.get("name", ""))
            
            self.x_entry.delete(0, "end")
            self.x_entry.insert(0, str(ci.x))
            
            self.y_entry.delete(0, "end")
            self.y_entry.insert(0, str(ci.y))
            
            self.scale_entry.delete(0, "end")
            self.scale_entry.insert(0, str(ci.scale))
            
            self.rotation_entry.delete(0, "end")
            self.rotation_entry.insert(0, str(ci.rotation))
            
            self.flip_h_var.set(bool(ci.flip_horizontal))
            self.flip_v_var.set(bool(ci.flip_vertical))
            self.visible_var.set(bool(ci.visible))
        except Exception as e:
            logger.error(f"Error loading item properties: {e}")
    
    def apply_props_from_entry(self, event=None):
        """Применяет свойства из полей ввода"""
        self.apply_props()
    
    def apply_props(self):
        """Применяет свойства к выбранным элементам"""
        if not self.current_selection:
            messagebox.showwarning("Нет выбора", "Сначала выберите элемент")
            return
        
        for ci in self.current_selection:
            # Имя
            name = self.name_entry.get().strip()
            if name:
                ci.layer["name"] = name
            
            # Координаты
            try:
                ci.x = int(self.x_entry.get().strip())
                ci.y = int(self.y_entry.get().strip())
            except ValueError:
                messagebox.showwarning("Ошибка", "X и Y должны быть целыми числами")
                return
            
            # Масштаб и поворот
            try:
                ci.scale = float(self.scale_entry.get().strip())
                ci.rotation = int(self.rotation_entry.get().strip())
            except ValueError:
                messagebox.showwarning("Ошибка", "Масштаб должен быть дробным числом, поворот - целым")
                return
            
            # Видимость и отражение
            ci.visible = self.visible_var.get()
            ci.flip_horizontal = self.flip_h_var.get()
            ci.flip_vertical = self.flip_v_var.get()
            
            # Очищаем кэш
            ci.clear_cache()
        
        self._save_tree_state()
        self.tree_state["preserve_selection"] = True
        
        self.refresh_tree()
        self._canvas_cache_valid = False
        self.redraw_canvas()
        self.last_autosave = time.time()
        
        self.save_to_history("Изменение свойств элемента")
    
    def ungroup_selected(self):
        """Разгруппировывает выделенные элементы"""
        if not self.selected_group and not self.current_selection:
            return
        
        if self.selected_group:
            group_name = self.selected_group
            
            # Получаем группу
            group = next((g for g in self.model.get("groups", []) if g.get("name") == group_name), None)
            if not group:
                return
                
            parent_group = group.get("parent")
            
            # Если есть родительская группа, переносим элементы в нее
            if parent_group:
                parent = next((g for g in self.model.get("groups", []) if g.get("name") == parent_group), None)
                if parent:
                    # Переносим элементы в родительскую группу
                    for ci in self.items:
                        if ci.layer.get("group") == group_name:
                            ci.layer["group"] = parent_group
                            if ci.layer.get("name") not in parent.get("children", []):
                                parent.setdefault("children", []).append(ci.layer.get("name"))
                    # Удаляем ссылку на группу из родительской группы
                    if group_name in parent.get("children", []):
                        parent["children"].remove(group_name)
            else:
                # Если нет родительской группы, удаляем группу у элементов
                for ci in self.items:
                    if ci.layer.get("group") == group_name:
                        ci.layer["group"] = None
            
            # Удаляем группу
            self.model["groups"] = [g for g in self.model.get("groups", []) if g.get("name") != group_name]
            
            # Удаляем все дочерние группы этой группы
            child_groups = [g for g in self.model.get("groups", []) if g.get("parent") == group_name]
            for child_group in child_groups:
                # Рекурсивно удаляем дочерние группы
                self._delete_group_and_children(child_group.get("name"))
                
            self.selected_group = parent_group
            
            # Сохраняем состояние дерева
            self._save_tree_state()
            self.tree_state["preserve_selection"] = True
            
            self.refresh_tree()
            self._canvas_cache_valid = False
            self.redraw_canvas()
            logger.info(f"Ungrouped group: {group_name}")
            self.save_to_history("Разгруппирование")
            return
            
        if not self.current_selection:
            return
            
        # Разгруппирование отдельных элементов
        for ci in self.current_selection:
            grp = ci.layer.get("group")
            if grp:
                for g in list(self.model.get("groups", [])):
                    if g.get("name") == grp and ci.layer.get("name") in g.get("children", []):
                        g["children"].remove(ci.layer.get("name"))
                        if not g["children"]:
                            # Если группа пуста, удаляем ее
                            self.model["groups"].remove(g)
                ci.layer["group"] = None
                
        # Сохраняем состояние дерева
        self._save_tree_state()
        self.tree_state["preserve_selection"] = True
        
        self.refresh_tree()
        self._canvas_cache_valid = False
        self.redraw_canvas()
        logger.info(f"Ungrouped {len(self.current_selection)} items")
        self.save_to_history("Разгруппирование элементов")

    def _delete_group_and_children(self, group_name):
        """Рекурсивно удаляет группу и все ее дочерние группы"""
        # Удаляем группу у элементов
        for ci in self.items:
            if ci.layer.get("group") == group_name:
                ci.layer["group"] = None
        
        # Удаляем дочерние группы
        child_groups = [g for g in self.model.get("groups", []) if g.get("parent") == group_name]
        for child_group in child_groups:
            self._delete_group_and_children(child_group.get("name"))
        
        # Удаляем саму группу
        self.model["groups"] = [g for g in self.model.get("groups", []) if g.get("name") != group_name]
    
    def update_group_logic_menus(self, group_name):
        """Обновляет меню логики группы"""
        group = next((g for g in self.model.get("groups", []) if g.get("name") == group_name), None)
        if not group:
            return
        
        # Собираем все возможные варианты: слои в группе и дочерние группы
        options = [""]
        
        # Добавляем слои, принадлежащие этой группе
        for layer in self.model.get("layers", []):
            if layer.get("group") == group_name:
                options.append(layer.get("name"))
        
        # Добавляем дочерние группы
        for g in self.model.get("groups", []):
            if g.get("parent") == group_name:
                options.append(g.get("name"))
        
        # Обновляем все меню для состояний
        for state in ["silent", "whisper", "normal", "shout", "blink", "open"]:
            menu_widget = getattr(self, f"{state}_menu")
            menu = menu_widget['menu']
            menu.delete(0, 'end')
            var = self.state_vars[state]
            # Добавляем новые опции
            for option in options:
                menu.add_command(
                    label=option,
                    command=lambda val=option, v=var: v.set(val)
                )
    
    def load_group_settings(self, group_name):
        """Загружает настройки группы"""
        group = next((g for g in self.model.get("groups", []) if g.get("name") == group_name), None)
        if not group:
            return
        
        self.update_group_logic_menus(group_name)
        
        logic = group.get("logic", {})
        for state in ["silent", "whisper", "normal", "shout", "blink", "open"]:
            if state in logic:
                self.state_vars[state].set(logic[state])
            else:
                self.state_vars[state].set("")
        
        # Настройки моргания
        blink_freq = group.get("blink_freq", 0.0)
        self.blink_freq.set(blink_freq)
        
        # Случайный эффект
        self.random_effect_var.set(group.get("random_effect", False))
        self.random_min_var.set(group.get("random_min", 5.0))
        self.random_max_var.set(group.get("random_max", 10.0))
    
    def on_canvas_mouse_down(self, event):
        """Обработчик нажатия мыши на холсте"""
        focus_widget = self.focus_get()
        if isinstance(focus_widget, (ttk.Entry, tk.Entry, ttk.Combobox)):
            return
        
        # Преобразуем координаты мыши в координаты холста с учетом зума
        canvas_width = self.canvas.winfo_width()
        canvas_height = self.canvas.winfo_height()
        scaled_width = int(self.canvas_width * self.zoom_level)
        scaled_height = int(self.canvas_height * self.zoom_level)
        
        center_x = canvas_width // 2 + self.offset_x
        center_y = canvas_height // 2 + self.offset_y
        
        canvas_x1 = center_x - scaled_width // 2
        canvas_y1 = center_y - scaled_height // 2
        
        # Координаты относительно холста с учетом зума
        mx = (event.x - canvas_x1) / self.zoom_level if self.zoom_level > 0 else 0
        my = (event.y - canvas_y1) / self.zoom_level if self.zoom_level > 0 else 0
        
        # Сбрасываем выделение
        for ci in self.items:
            ci.layer["_selected"] = False
        
        self.current_selection = []
        self.drag_data = {"item": None, "x": mx, "y": my, "group_items": []}
        found = None
        found_items = []  # Собираем все элементы под курсором
        
        # Ищем элемент под курсором (с конца для правильного Z-порядка)
        for ci in reversed(self.items):
            if not ci.visible:
                continue
            
            img = ci.get_image_for_display(self.zoom_level)
            if img is None:
                continue
            
            # Позиция элемента
            img_center_x = scaled_width // 2
            img_center_y = scaled_height // 2
            
            pos_x = img_center_x - img.width // 2 + int(ci.x * self.zoom_level)
            pos_y = img_center_y - img.height // 2 + int(ci.y * self.zoom_level)
            
            # Проверяем попадание
            if (pos_x <= event.x - canvas_x1 <= pos_x + img.width and
                pos_y <= event.y - canvas_y1 <= pos_y + img.height):
                
                # Проверяем прозрачность пикселя
                try:
                    is_opaque = False
                    if img.mode == 'RGBA':
                        pixel_x = int((event.x - canvas_x1) - pos_x)
                        pixel_y = int((event.y - canvas_y1) - pos_y)
                        
                        if 0 <= pixel_x < img.width and 0 <= pixel_y < img.height:
                            pixel = img.getpixel((pixel_x, pixel_y))
                            if len(pixel) >= 4 and pixel[3] > 10:  # Порог прозрачности
                                is_opaque = True
                    else:
                        # Для непрозрачных форматов всегда считаем непрозрачным
                        is_opaque = True
                    
                    if is_opaque:
                        found_items.append(ci)  # Добавляем в список
                        
                except Exception as e:
                    logger.error(f"Error checking pixel: {e}")
                    found_items.append(ci)  # В случае ошибки тоже добавляем
        
        # ВЫБИРАЕМ САМЫЙ ВЕРХНИЙ НЕПРОЗРАЧНЫЙ ЭЛЕМЕНТ
        if found_items:
            found = found_items[0]  # Берем первый (самый верхний) элемент
        
        if found:
            # Выделяем элемент
            if event.state & 0x0004:  # Ctrl
                found.layer["_selected"] = not found.layer.get("_selected", False)
                if found.layer["_selected"]:
                    self.current_selection.append(found)
                elif found in self.current_selection:
                    self.current_selection.remove(found)
            else:
                found.layer["_selected"] = True
                self.current_selection = [found]
            
            self.drag_data["item"] = found
            self.drag_data["group_items"] = self.current_selection.copy()
            
            # ВЫДЕЛЯЕМ ЭЛЕМЕНТ В ДЕРЕВЕ
            self._select_item_in_tree(found)
            
            # Загружаем свойства элемента
            if self.current_selection:
                self.load_item_props(self.current_selection[0])
        else:
            # Сбрасываем выделение
            self.selected_group = None
            self.group_label.config(text="(нет группы)")
            self.clear_props_fields()
            
            # СНИМАЕМ ВЫДЕЛЕНИЕ В ДЕРЕВЕ
            self.tree.selection_remove(self.tree.selection())
        
        self._canvas_cache_valid = False
        self.redraw_canvas()
    
    def on_canvas_mouse_move(self, event):
        """Обработчик перемещения мыши с зажатой кнопкой"""
        if not self.drag_data.get("item") or not self.drag_data.get("group_items"):
            return
        
        # Преобразуем координаты
        canvas_width = self.canvas.winfo_width()
        canvas_height = self.canvas.winfo_height()
        scaled_width = int(self.canvas_width * self.zoom_level)
        scaled_height = int(self.canvas_height * self.zoom_level)
        
        center_x = canvas_width // 2 + self.offset_x
        center_y = canvas_height // 2 + self.offset_y
        
        canvas_x1 = center_x - scaled_width // 2
        canvas_y1 = center_y - scaled_height // 2
        
        mx = (event.x - canvas_x1) / self.zoom_level if self.zoom_level > 0 else 0
        my = (event.y - canvas_y1) / self.zoom_level if self.zoom_level > 0 else 0
        
        dx = mx - self.drag_data["x"]
        dy = my - self.drag_data["y"]
        
        self.drag_data["x"] = mx
        self.drag_data["y"] = my
        
        # Перемещаем элементы
        for ci in self.drag_data["group_items"]:
            ci.x += int(dx)
            ci.y += int(dy)
        
        # Обновляем поля ввода
        if len(self.current_selection) == 1:
            self.x_entry.delete(0, "end")
            self.x_entry.insert(0, str(self.current_selection[0].x))
            self.y_entry.delete(0, "end")
            self.y_entry.insert(0, str(self.current_selection[0].y))
        
        self._canvas_cache_valid = False
        self.redraw_canvas()
    
    def on_canvas_mouse_up(self, event):
        """Обработчик отпускания кнопки мыши"""
        self.drag_data["item"] = None
        self.last_autosave = time.time()
        self.save_to_history("Перемещение элемента")
    
    def add_to_canvas(self, filename):
        """Добавляет файл на холст"""
        for fname, img, is_gif in self.imported_files:
            if fname == filename:
                # Ищем слой
                layer = None
                for l in self.model.get("layers", []):
                    if l.get("file") == fname:
                        layer = l
                        break
                
                if not layer:
                    layer = {
                        "name": os.path.splitext(fname)[0], 
                        "file": fname, 
                        "visible": True, 
                        "x": 0, 
                        "y": 0,
                        "scale": 1.0,
                        "rotation": 0,
                        "group": None,
                        "is_gif": is_gif,
                        "flip_horizontal": False,
                        "flip_vertical": False
                    }
                    self.model.setdefault("layers", []).append(layer)
                
                # Создаем элемент
                image_path = os.path.join(self.model_dir, fname)
                ci = CanvasItem(layer, image_path)
                self.items.append(ci)
                
                self._save_tree_state()
                self.tree_state["preserve_selection"] = True
                
                self.refresh_tree()
                self._canvas_cache_valid = False
                self.redraw_canvas()
                return
    
    def remove_from_canvas_by_file(self, filename):
        """Удаляет файл с холста"""
        new_items = [ci for ci in self.items if ci.layer.get("file") != filename]
        if len(new_items) != len(self.items):
            self.items = new_items
            
            # Удаляем выделение
            for l in self.model.get("layers", []):
                if l.get("file") == filename:
                    l["_selected"] = False
            
            self._save_tree_state()
            self.tree_state["preserve_selection"] = True
            
            self.refresh_tree()
            self._canvas_cache_valid = False
            self.redraw_canvas()
    
    def delete_file(self, filename):
        """Удаляет файл полностью"""
        if messagebox.askyesno("Удаление файла", f"Удалить {filename} навсегда?"):
            self.remove_from_canvas_by_file(filename)
            self.imported_files = [f for f in self.imported_files if f[0] != filename]
            
            if self.model_dir:
                file_path = os.path.join(self.model_dir, filename)
                if os.path.exists(file_path):
                    os.remove(file_path)
            
            self.model["layers"] = [l for l in self.model["layers"] if l.get("file") != filename]
            self.refresh_import_list()
            
            self._save_tree_state()
            self.tree_state["preserve_selection"] = True
            
            self.refresh_tree()
            self._canvas_cache_valid = False
            self.redraw_canvas()
            
            logger.info(f"File deleted: {filename}")
            self.save_to_history("Удаление файла")
    
    def show_blink_preview(self):
        """Показывает превью моргания"""
        if not self.selected_group:
            return
        
        self.blink_preview_running = True
        self._blink_preview_loop()
        logger.info("Blink preview started")
    
    def stop_blink_preview(self):
        """Останавливает превью моргания"""
        self.blink_preview_running = False
        logger.info("Blink preview stopped")
    
    def _blink_preview_loop(self):
        if not self.blink_preview_running:
            return
            
        gname = self.selected_group
        group = next((g for g in self.model.get("groups", []) if g.get("name") == gname), None)
        if not group:
            return
            
        blink_freq = float(group.get("blink_freq", 0.0))
        if blink_freq < 0.1:
            return
            
        logic = group.get("logic", {})
        blink_layer = logic.get("blink", "")
        
        # Скрываем все элементы в группе
        for ci in self.items:
            if ci.layer.get("group") == gname:
                # Показываем только слой для моргания
                ci.visible = (ci.layer.get("name") == blink_layer)
                
        self.redraw_canvas(0, "none")
        self.after(200, self._show_normal_preview)
    
    def _show_normal_preview(self):
        if not self.blink_preview_running:
            return
            
        gname = self.selected_group
        group = next((g for g in self.model.get("groups", []) if g.get("name") == gname), None)
        if not group:
            return
            
        logic = group.get("logic", {})
        open_layer = logic.get("open") or logic.get("normal") or logic.get("whisper") or logic.get("silent")
        
        # Скрываем все элементы в группе
        for ci in self.items:
            if ci.layer.get("group") == gname:
                # Показываем только основной слой
                ci.visible = (ci.layer.get("name") == open_layer)
                
        self.redraw_canvas(0, "none")
        
        blink_freq = float(group.get("blink_freq", 0.0))
        if blink_freq > 0.1:
            self.after(int(blink_freq * 1000), self._blink_preview_loop)
    
    def _preview_loop(self):
        """Основной цикл предпросмотра"""
        try:
            # Автосохранение
            now = time.time()
            if now - self.last_autosave > self.autosave_interval:
                try:
                    if self.model_dir:
                        temp = {
                            "name": self.model.get("name", ""), 
                            "width": self.model.get("width", 700),
                            "height": self.model.get("height", 700),
                            "layers": [], 
                            "groups": self.model.get("groups", [])
                        }
                        for ci in self.items:
                            temp["layers"].append({
                                "name": ci.layer.get("name"),
                                "file": os.path.basename(ci.layer.get("file")),
                                "x": int(ci.x),
                                "y": int(ci.y),
                                "visible": bool(ci.visible),
                                "is_gif": ci.is_gif,
                                "scale": float(ci.scale),
                                "rotation": int(ci.rotation),
                                "group": ci.layer.get("group", None),
                                "flip_horizontal": bool(ci.flip_horizontal),
                                "flip_vertical": bool(ci.flip_vertical)
                            })
                        with open(os.path.join(self.model_dir, "model.json"), "w", encoding="utf-8") as f:
                            json.dump(temp, f, indent=2, ensure_ascii=False)
                except Exception as e:
                    logger.error(f"Error autosaving: {e}")
                self.last_autosave = now
            
            # Обновляем отображение в зависимости от режима
            mode = self.test_mode_var.get()
            level = self.audio_level if mode == "microphone" else 0.0
            
            self.redraw_canvas(level, mode)
            
        except Exception as e:
            logger.error(f"Error in preview loop: {e}")
        finally:
            if self.winfo_exists():
                self.after(int(1000 / self.preview_fps), self._preview_loop)