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
import zipfile
import tempfile
from typing import Dict, List, Set, Optional, Tuple
from collections import deque
from dataclasses import dataclass
from enum import Enum
import hashlib

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
    logger.setLevel(logging.INFO)  # Уменьшили уровень логирования
    
    formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    
    log_file = os.path.join(LOGS_DIR, 'editor.log')
    file_handler = logging.handlers.RotatingFileHandler(
        log_file, maxBytes=1048576, backupCount=5
    )
    file_handler.setFormatter(formatter)
    
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    
    logger.addHandler(file_handler)
    logger.addHandler(console_handler)
    
    return logger

logger = setup_editor_logging()

MODELS_DIR = os.path.join(BASE_DIR, "models")
os.makedirs(MODELS_DIR, exist_ok=True)

# Оптимизированные структуры данных
@dataclass
class LayerData:
    """Оптимизированное хранение данных слоя"""
    name: str
    file: str
    x: int = 0
    y: int = 0
    scale: float = 1.0
    rotation: int = 0
    flip_h: bool = False
    flip_v: bool = False
    visible: bool = True
    is_gif: bool = False
    group: Optional[str] = None
    
    def to_dict(self):
        return {
            "name": self.name,
            "file": self.file,
            "x": self.x,
            "y": self.y,
            "scale": self.scale,
            "rotation": self.rotation,
            "flip_horizontal": self.flip_h,
            "flip_vertical": self.flip_v,
            "visible": self.visible,
            "is_gif": self.is_gif,
            "group": self.group
        }

@dataclass
class GroupData:
    """Оптимизированное хранение данных группы"""
    name: str
    children: List[str]
    parent: Optional[str] = None
    logic: Dict = None
    blink_freq: float = 0.0
    random_effect: bool = False
    random_min: float = 5.0
    random_max: float = 10.0
    
    def __post_init__(self):
        if self.logic is None:
            self.logic = {}

class OptimizedCanvasItem:
    """Оптимизированный элемент канваса"""
    __slots__ = ['layer', 'image_path', 'is_gif', 'scale', 'rotation', 
                 'x', 'y', 'flip_h', 'flip_v', 'visible', 'gif_frames',
                 'current_frame', 'last_frame_time', 'frame_durations',
                 '_transformed_cache', '_transform_hash', '_selected']
    
    def __init__(self, layer: LayerData, image_path: str):
        self.layer = layer
        self.image_path = image_path
        self.is_gif = layer.is_gif
        self.scale = layer.scale
        self.rotation = layer.rotation
        self.x = layer.x
        self.y = layer.y
        self.flip_h = layer.flip_h
        self.flip_v = layer.flip_v
        self.visible = layer.visible
        self._selected = False  # Добавляем атрибут выделения ← УБЕДИТЕСЬ ЧТО ЭТО ЕСТЬ
        
        # GIF анимация
        self.gif_frames: List[Image.Image] = []
        self.current_frame = 0
        self.last_frame_time = 0
        self.frame_durations: List[float] = []
        
        # Кэширование трансформаций
        self._transformed_cache: Optional[Image.Image] = None
        self._transform_hash: str = ""
        
        # Предзагрузка изображения
        self._preload_image()
    
    def _preload_image(self):
        """Предварительная загрузка и кэширование изображения"""
        try:
            if self.is_gif:
                with Image.open(self.image_path) as gif:
                    gif.seek(0)
                    base_image = gif.copy().convert("RGBA")
                    
                    # Загружаем все кадры
                    for frame in range(gif.n_frames):
                        gif.seek(frame)
                        frame_img = gif.copy().convert("RGBA")
                        frame_img = self._apply_transformations(frame_img)
                        self.gif_frames.append(frame_img)
                        
                        try:
                            duration = gif.info.get('duration', 100) / 1000.0
                            self.frame_durations.append(duration)
                        except:
                            self.frame_durations.append(0.1)
                    
                    # Кэшируем первый кадр как трансформированный
                    self._transformed_cache = self.gif_frames[0] if self.gif_frames else None
            else:
                image = Image.open(self.image_path).convert("RGBA")
                self._transformed_cache = self._apply_transformations(image)
            
            # Сохраняем хэш трансформаций
            self._update_transform_hash()
            
        except Exception as e:
            logger.error(f"Ошибка предзагрузки изображения: {e}")
            self._transformed_cache = Image.new("RGBA", (100, 100), (255, 0, 0, 128))
    
    def _apply_transformations(self, img: Image.Image) -> Image.Image:
        """Применяет трансформации к изображению"""
        if not img:
            return img
        
        transformed = img.copy()
        
        # Порядок трансформаций: отражение → масштаб → поворот
        if self.flip_h:
            transformed = transformed.transpose(Image.FLIP_LEFT_RIGHT)
        if self.flip_v:
            transformed = transformed.transpose(Image.FLIP_TOP_BOTTOM)
        
        if self.scale != 1.0:
            new_width = max(1, int(transformed.width * self.scale))
            new_height = max(1, int(transformed.height * self.scale))
            transformed = transformed.resize((new_width, new_height), Image.LANCZOS)
        
        if self.rotation != 0:
            transformed = transformed.rotate(self.rotation, expand=True, resample=Image.BICUBIC)
        
        return transformed
    
    def _update_transform_hash(self):
        """Обновляет хэш трансформаций"""
        transform_str = f"{self.scale}_{self.rotation}_{self.flip_h}_{self.flip_v}"
        self._transform_hash = hashlib.md5(transform_str.encode()).hexdigest()
    
    def update_transformed_image(self):
        """Обновляет трансформированное изображение"""
        self._transformed_cache = None
        self._preload_image()
    
    def get_current_image(self) -> Optional[Image.Image]:
        """Возвращает текущее изображение с кэшированием"""
        if self.is_gif and self.gif_frames:
            now = time.time()
            if now - self.last_frame_time > self.frame_durations[self.current_frame]:
                self.current_frame = (self.current_frame + 1) % len(self.gif_frames)
                self.last_frame_time = now
            return self.gif_frames[self.current_frame]
        
        return self._transformed_cache

class ThrottledCanvas:
    """Канвас с троттлингом отрисовки"""
    def __init__(self, canvas: tk.Canvas, min_interval: float = 0.016):
        self.canvas = canvas
        self.min_interval = min_interval
        self.last_draw_time = 0
        self.pending_redraw = False
    
    def schedule_redraw(self, callback, *args, **kwargs):
        """Планирует отрисовку с троттлингом"""
        current_time = time.time()
        
        if current_time - self.last_draw_time >= self.min_interval:
            # Можно отрисовать сразу
            callback(*args, **kwargs)
            self.last_draw_time = current_time
            self.pending_redraw = False
        elif not self.pending_redraw:
            # Планируем отрисовку позже
            self.pending_redraw = True
            delay = int((self.min_interval - (current_time - self.last_draw_time)) * 1000)
            self.canvas.after(max(10, delay), lambda: self._execute_redraw(callback, args, kwargs))
    
    def _execute_redraw(self, callback, args, kwargs):
        """Выполняет отложенную отрисовку"""
        callback(*args, **kwargs)
        self.last_draw_time = time.time()
        self.pending_redraw = False

class OptimizedImageCache:
    """Кэш для оптимизированного хранения изображений редактора"""
    def __init__(self, max_size: int = 100):
        self.max_size = max_size
        self._cache: Dict[str, Image.Image] = {}
        self._access_order: List[str] = []
    
    def get(self, key: str, loader: callable) -> Optional[Image.Image]:
        """Получает изображение из кэша или загружает его"""
        if key in self._cache:
            # Обновляем порядок доступа
            self._access_order.remove(key)
            self._access_order.append(key)
            return self._cache[key]
        
        # Загружаем новое изображение
        image = loader()
        if image:
            self._cache[key] = image
            self._access_order.append(key)
            
            # Очищаем старые записи если нужно
            if len(self._cache) > self.max_size:
                oldest_key = self._access_order.pop(0)
                del self._cache[oldest_key]
        
        return image
    
    def clear(self):
        """Очищает кэш"""
        self._cache.clear()
        self._access_order.clear()

class ModelEditor(tk.Toplevel):
    def __init__(self, master, on_save=None, device='По умолчанию', 
                 noise_gate_threshold=0.01, sensitivity=1.0, thresholds=None, 
                 current_slot=None):
        super().__init__(master)
        
        self.title("Редактор моделей")
        self.geometry("1400x800")
        
        self.on_save = on_save
        self.protocol("WM_DELETE_WINDOW", self.on_close)
        self.current_slot = current_slot
        
        logger.info(f"Model editor opened, current slot: {current_slot}")
        
        # Сохраняем настройки
        self.mic_device = device
        self.mic_noise_gate_threshold = noise_gate_threshold
        self.mic_sensitivity = sensitivity
        self.thresholds = thresholds or {
            'silent': 0.05,
            'whisper': 0.25,
            'normal': 0.6,
            'shout': 0.8
        }
        
        # Оптимизированные структуры данных
        self.model_name = "Без названия"
        self.width = 700
        self.height = 700
        
        self.layers: Dict[str, LayerData] = {}
        self.groups: Dict[str, GroupData] = {}
        self.items: List[OptimizedCanvasItem] = []
        
        self.imported_files = []
        self.drag_data = {"item": None, "x": 0, "y": 0, "group_items": []}
        self.selected_group = None
        self.current_selection: List[OptimizedCanvasItem] = []
        
        # Оптимизации
        self.preview_fps = 30  # Уменьшили FPS превью
        self.last_autosave = time.time()
        self.autosave_interval = 10.0  # Увеличили интервал автосохранения
        
        # Кэши
        self.image_cache = OptimizedImageCache(max_size=50)
        self.visible_items_cache: List[OptimizedCanvasItem] = []
        self.visible_cache_time = 0
        self.cache_valid_for = 0.2  # Секунд
        
        # Троттлинг отрисовки
        self.canvas_redraw_throttled = None
        
        # Настройки зума
        self.zoom_level = 1.0
        self.zoom_step = 0.1
        self.min_zoom = 0.1
        self.max_zoom = 5.0
        self.offset_x = 0
        self.offset_y = 0
        self.is_panning = False
        self.last_pan_x = 0
        self.last_pan_y = 0
        
        # Таймеры анимаций
        self.group_blink_timers = {}
        self.group_blink_until = {}
        self.group_random_timers = {}
        self.group_random_current = {}
        
        # Состояние дерева
        self.tree_state = {
            "expanded_groups": set(),
            "selected_items": set(),
            "preserve_selection": False
        }
        
        # История
        self.history = []
        self.history_index = -1
        self.max_history_size = 30  # Уменьшили размер истории
        
        # Аудиопроцессор
        self.audio_level = 0.0
        self.blink_preview_running = False
        self.audio_processor = AudioProcessor(
            callback=self.on_audio_level,
            device=self.mic_device
        )
        self.audio_processor.noise_gate_threshold = self.mic_noise_gate_threshold
        self.audio_processor.set_sensitivity(self.mic_sensitivity)
        
        # Очищаем старые временные папки
        self.cleanup_old_temp_folders()
        
        # Создаем UI
        self._create_ui()
        
        # Сохраняем начальное состояние
        self._save_to_history("Инициализация")
        
        # Запускаем превью
        self.after(100, self._optimized_preview_loop)
    
    def _create_ui(self):
        """Создает оптимизированный UI"""
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
        name_entry.bind("<Return>", self._update_model_name)
        
        # Кнопки управления
        btn_frame = ttk.Frame(left)
        btn_frame.pack(fill="x", pady=2)
        
        ttk.Button(btn_frame, text="Новая", command=self.new_model, width=8).pack(side="left", padx=1, fill="x", expand=True)
        ttk.Button(btn_frame, text="Загрузить", command=self.load_model, width=8).pack(side="left", padx=1, fill="x", expand=True)
        ttk.Button(btn_frame, text="Сохранить", command=self.save_model, width=8).pack(side="left", padx=1, fill="x", expand=True)
        
        btn_frame2 = ttk.Frame(left)
        btn_frame2.pack(fill="x", pady=2)
        ttk.Button(btn_frame2, text="Импорт PNG/GIF", command=self.import_images, width=12).pack(side="left", padx=1, fill="x", expand=True)
        ttk.Button(btn_frame2, text="Удалить модель", command=self.delete_model, width=12).pack(side="left", padx=1, fill="x", expand=True)
        
        btn_frame3 = ttk.Frame(left)
        btn_frame3.pack(fill="x", pady=2)
        ttk.Button(btn_frame3, text="Экспорт ZIP", command=self.export_zip, width=10).pack(side="left", padx=1, fill="x", expand=True)
        ttk.Button(btn_frame3, text="Импорт ZIP", command=self.import_zip, width=10).pack(side="left", padx=1, fill="x", expand=True)
        
        # Настройки холста
        canvas_frame = ttk.LabelFrame(left, text="Настройки холста")
        canvas_frame.pack(fill="x", pady=10)
        
        ttk.Label(canvas_frame, text="Ширина:").grid(row=0, column=0, sticky="w", padx=2, pady=2)
        self.canvas_width_var = tk.IntVar(value=self.width)
        width_entry = ttk.Entry(canvas_frame, textvariable=self.canvas_width_var, width=8)
        width_entry.grid(row=0, column=1, sticky="ew", padx=2, pady=2)
        width_entry.bind("<Return>", self.update_canvas_size)
        
        ttk.Label(canvas_frame, text="Высота:").grid(row=1, column=0, sticky="w", padx=2, pady=2)
        self.canvas_height_var = tk.IntVar(value=self.height)
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
        self.level_bar = ttk.Progressbar(self.level_frame, length=150, mode="determinate")
        self.level_bar.pack(side="left", fill="x", expand=True, padx=5)
        
        # Список импортированных изображений
        ttk.Label(left, text="Импортированные изображения:").pack(anchor="w", pady=(8, 0))
        
        import_frame = ttk.Frame(left)
        import_frame.pack(fill="both", expand=True)
        
        self.import_canvas = tk.Canvas(import_frame, width=280, height=200)
        self.import_vscroll = ttk.Scrollbar(import_frame, orient="vertical", command=self.import_canvas.yview)
        self.import_canvas.configure(yscrollcommand=self.import_vscroll.set)
        
        self.import_vscroll.pack(side="right", fill="y")
        self.import_canvas.pack(side="left", fill="both", expand=True)
        
        self.import_inner = ttk.Frame(self.import_canvas)
        self.import_canvas.create_window((0, 0), window=self.import_inner, anchor="nw")
        self.import_inner.bind("<Configure>", lambda e: self.import_canvas.configure(scrollregion=self.import_canvas.bbox("all")))
        
        # ---- Центральная панель ----
        preview_header = ttk.Frame(center)
        preview_header.pack(fill="x", padx=5, pady=(0, 5))
        
        ttk.Label(preview_header, text="Предпросмотр", font=("Arial", 10, "bold")).pack(side="left", padx=(0, 10))
        
        undo_btn = ttk.Button(preview_header, text="←", width=2, command=self.undo)
        undo_btn.pack(side="left", padx=2)
        redo_btn = ttk.Button(preview_header, text="→", width=2, command=self.redo)
        redo_btn.pack(side="left", padx=2)
        
        preview_frame = ttk.Frame(center)
        preview_frame.pack(fill="both", expand=True)
        
        # Канвас с троттлингом
        self.canvas = tk.Canvas(preview_frame, bg="#222", cursor="crosshair")
        self.canvas.pack(fill="both", expand=True, padx=5, pady=5)
        self.canvas_redraw_throttled = ThrottledCanvas(self.canvas, min_interval=0.033)
        
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
        
        # Горячие клавиши
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
        
        # Древовидный список
        items_list_frame = ttk.Frame(items_frame)
        items_list_frame.pack(fill="both", expand=True, pady=(0, 5))
        
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
        
        ttk.Label(grid_frame, text="X:").grid(row=1, column=0, sticky="w", padx=2, pady=2)
        self.x_entry = ttk.Entry(grid_frame)
        self.x_entry.grid(row=1, column=1, sticky="ew", padx=2, pady=2)
        self.x_entry.bind("<Return>", self.apply_props_from_entry)
        
        ttk.Label(grid_frame, text="Y:").grid(row=2, column=0, sticky="w", padx=2, pady=2)
        self.y_entry = ttk.Entry(grid_frame)
        self.y_entry.grid(row=2, column=1, sticky="ew", padx=2, pady=2)
        self.y_entry.bind("<Return>", self.apply_props_from_entry)
        
        ttk.Label(grid_frame, text="Масштаб:").grid(row=3, column=0, sticky="w", padx=2, pady=2)
        self.scale_entry = ttk.Entry(grid_frame)
        self.scale_entry.grid(row=3, column=1, sticky="ew", padx=2, pady=2)
        self.scale_entry.bind("<Return>", self.apply_props_from_entry)
        
        ttk.Label(grid_frame, text="Поворот:").grid(row=4, column=0, sticky="w", padx=2, pady=2)
        self.rotation_entry = ttk.Entry(grid_frame)
        self.rotation_entry.grid(row=4, column=1, sticky="ew", padx=2, pady=2)
        self.rotation_entry.bind("<Return>", self.apply_props_from_entry)
        
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
        
        self.visible_var = tk.BooleanVar(value=True)
        self.visible_cb = ttk.Checkbutton(props, text="Видимый", variable=self.visible_var)
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
            
            om = ttk.OptionMenu(row, self.state_vars[s], "")
            om.pack(side="left", fill="x", expand=True)
            setattr(self, f"{s}_menu", om)
        
        # Случайный эффект
        random_frame = ttk.LabelFrame(groups_frame, text="Случайный эффект")
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
        blink_frame = ttk.LabelFrame(groups_frame, text="Настройки моргания")
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
        ttk.Button(btn_frame, text="Стоп", width=8, command=self.stop_blink_preview).pack(side="left", padx=2)
        
        ttk.Button(groups_frame, text="Применить логику", command=self.apply_group_logic).pack(fill="x", pady=10)
        
        try:
            self.iconbitmap(os.path.join(BASE_DIR, 'favicon.ico'))
        except Exception as e:
            logger.error(f"Error loading icon: {e}")
    
    # Оптимизированные методы
    def _save_to_history(self, description=""):
        """Сохраняет состояние в историю"""
        if self.history_index < len(self.history) - 1:
            self.history = self.history[:self.history_index + 1]
        
        # Сохраняем только минимально необходимые данные
        state = {
            'model_name': self.model_name,
            'width': self.width,
            'height': self.height,
            'layers': {name: layer.to_dict() for name, layer in self.layers.items()},
            'groups': {name: group.__dict__ for name, group in self.groups.items()},
            'description': description
        }
        
        self.history.append(state)
        self.history_index = len(self.history) - 1
        
        if len(self.history) > self.max_history_size:
            self.history.pop(0)
            self.history_index -= 1
    
    def _load_from_history(self):
        """Загружает состояние из истории"""
        if self.history_index < 0 or self.history_index >= len(self.history):
            return
        
        state = self.history[self.history_index]
        
        try:
            # Восстанавливаем модель
            self.model_name = state['model_name']
            self.width = state['width']
            self.height = state['height']
            
            # Восстанавливаем слои - исправляем здесь
            self.layers.clear()
            for name, layer_data in state['layers'].items():
                # Фильтруем только ожидаемые поля для LayerData
                valid_layer_data = {}
                valid_fields = ["name", "file", "x", "y", "scale", "rotation", 
                            "flip_horizontal", "flip_vertical", "visible", 
                            "is_gif", "group"]
                
                for field in valid_fields:
                    if field in layer_data:
                        valid_layer_data[field] = layer_data[field]
                
                # Переименовываем поля для соответствия LayerData
                if "flip_horizontal" in valid_layer_data:
                    valid_layer_data["flip_h"] = valid_layer_data.pop("flip_horizontal")
                if "flip_vertical" in valid_layer_data:
                    valid_layer_data["flip_v"] = valid_layer_data.pop("flip_vertical")
                
                self.layers[name] = LayerData(**valid_layer_data)
            
            # Восстанавливаем группы - исправляем здесь
            self.groups.clear()
            for name, group_data in state['groups'].items():
                # Фильтруем только ожидаемые поля для GroupData
                valid_group_data = {}
                valid_fields = ["name", "children", "parent", "logic", 
                            "blink_freq", "random_effect", "random_min", "random_max"]
                
                for field in valid_fields:
                    if field in group_data:
                        valid_group_data[field] = group_data[field]
                
                # Убедимся, что logic - это словарь
                if "logic" not in valid_group_data:
                    valid_group_data["logic"] = {}
                
                self.groups[name] = GroupData(**valid_group_data)
            
            # Обновляем UI
            self.model_name_var.set(self.model_name)
            self.canvas_width_var.set(self.width)
            self.canvas_height_var.set(self.height)
            
            # Перестраиваем items
            self._rebuild_items_from_layers()
            
            # Обновляем дерево
            self.refresh_tree()
            
            # Инвалидируем кэши
            self.visible_items_cache = []
            
        except Exception as e:
            logger.error(f"Error loading from history: {e}")
    
    def _rebuild_items_from_layers(self):
        """Перестраивает items из layers"""
        self.items.clear()
        
        for layer_name, layer_data in self.layers.items():
            # Ищем путь к файлу
            filepath = None
            for fname, _, _ in self.imported_files:
                if fname == layer_data.file:
                    # Пытаемся найти файл
                    if self.model_dir:
                        filepath = os.path.join(self.model_dir, fname)
                        if not os.path.exists(filepath):
                            # Ищем в других местах
                            for root, dirs, files in os.walk(self.model_dir):
                                if fname in files:
                                    filepath = os.path.join(root, fname)
                                    break
                    break
            
            if filepath and os.path.exists(filepath):
                item = OptimizedCanvasItem(layer_data, filepath)
                self.items.append(item)
    
    def _get_visible_items_cached(self) -> List[OptimizedCanvasItem]:
        """Получает видимые элементы с кэшированием"""
        current_time = time.time()
        
        if (self.visible_items_cache and 
            current_time - self.visible_cache_time < self.cache_valid_for):
            return self.visible_items_cache
        
        # Фильтруем видимые элементы
        visible_items = [item for item in self.items if item.visible]
        self.visible_items_cache = visible_items
        self.visible_cache_time = current_time
        
        return visible_items
    
    def _optimized_redraw_canvas(self, level=0.0, mode="none"):
        """Оптимизированная перерисовка канваса"""
        try:
            self.canvas.delete("all")
            
            canvas_width = self.canvas.winfo_width()
            canvas_height = self.canvas.winfo_height()
            
            if canvas_width <= 1 or canvas_height <= 1:
                return
            
            # Рассчитываем параметры отображения
            scaled_width = self.width * self.zoom_level
            scaled_height = self.height * self.zoom_level
            center_x = canvas_width // 2 + self.offset_x
            center_y = canvas_height // 2 + self.offset_y
            
            # Рисуем рамку холста
            canvas_x1 = center_x - scaled_width // 2
            canvas_y1 = center_y - scaled_height // 2
            canvas_x2 = center_x + scaled_width // 2
            canvas_y2 = center_y + scaled_height // 2
            
            self.canvas.create_rectangle(
                canvas_x1, canvas_y1, canvas_x2, canvas_y2,
                outline="#666", width=2, fill="#333"
            )
            
            # Создаем временное изображение
            temp_image = Image.new("RGBA", (int(scaled_width), int(scaled_height)), (0, 0, 0, 0))
            
            # Определяем элементы для отрисовки
            items_to_draw = []
            
            if mode == "none":
                # Режим редактирования
                items_to_draw = self._get_visible_items_cached()
            else:
                # Режим тестирования
                current_state = "silent"
                if level > self.thresholds['shout']:
                    current_state = "shout"
                elif level > self.thresholds['normal']:
                    current_state = "normal"
                elif level > self.thresholds['whisper']:
                    current_state = "whisper"
                elif level > self.thresholds['silent']:
                    current_state = "silent"
                
                items_to_draw = self._get_visible_items_for_state(current_state)
            
            # Сортируем по порядку
            items_to_draw.sort(key=lambda x: self.items.index(x))
            
            # Отрисовываем элементы
            for item in items_to_draw:
                if not item.visible:
                    continue
                
                img = item.get_current_image()
                if not img:
                    continue
                
                # Масштабируем для текущего зума
                if self.zoom_level != 1.0:
                    scaled_img_width = int(img.width * self.zoom_level)
                    scaled_img_height = int(img.height * self.zoom_level)
                    if scaled_img_width > 0 and scaled_img_height > 0:
                        scaled_img = img.resize((scaled_img_width, scaled_img_height), Image.LANCZOS)
                    else:
                        scaled_img = img
                else:
                    scaled_img = img
                
                # Рассчитываем позицию
                px = int((scaled_width // 2) - scaled_img.width // 2 + (item.x * self.zoom_level))
                py = int((scaled_height // 2) - scaled_img.height // 2 + (item.y * self.zoom_level))
                
                try:
                    temp_image.alpha_composite(scaled_img, (px, py))
                except Exception as e:
                    logger.error(f"Ошибка композиции: {e}")
            
            # Конвертируем в PhotoImage
            try:
                self.canvas_image = ImageTk.PhotoImage(temp_image)
                self.canvas.create_image(canvas_x1, canvas_y1, anchor="nw", image=self.canvas_image)
            except Exception as e:
                logger.error(f"Ошибка отображения: {e}")
            
            # Выделение в режиме редактирования
            if mode == "none":
                for item in self.items:
                    if hasattr(item, '_selected') and item._selected:
                        img = item.get_current_image()
                        if not img:
                            continue
                        
                        if self.zoom_level != 1.0:
                            scaled_img_width = int(img.width * self.zoom_level)
                            scaled_img_height = int(img.height * self.zoom_level)
                        else:
                            scaled_img_width = img.width
                            scaled_img_height = img.height
                        
                        px = canvas_x1 + int((scaled_width // 2) - scaled_img_width // 2 + (item.x * self.zoom_level))
                        py = canvas_y1 + int((scaled_height // 2) - scaled_img_height // 2 + (item.y * self.zoom_level))
                        
                        self.canvas.create_rectangle(
                            px, py, px + scaled_img_width, py + scaled_img_height,
                            outline="cyan", width=2
                        )
                        
        except Exception as e:
            logger.error(f"Error in redraw_canvas: {e}")
    
    def redraw_canvas(self, level=0.0, mode="none"):
        """Публичный метод с троттлингом"""
        if self.canvas_redraw_throttled:
            self.canvas_redraw_throttled.schedule_redraw(
                self._optimized_redraw_canvas, level, mode
            )
    
    def _get_visible_items_for_state(self, current_state: str) -> List[OptimizedCanvasItem]:
        """Получает видимые элементы для текущего состояния"""
        visible_items = []
        processed_groups = set()
        
        def process_group(group_name: str):
            if group_name in processed_groups:
                return
            processed_groups.add(group_name)
            
            group = self.groups.get(group_name)
            if not group:
                return
            
            chosen = self._get_current_state_for_group(group_name)
            
            if not chosen:
                # Показываем все видимые слои группы
                for item in self.items:
                    if item.layer.group == group_name and item.visible:
                        visible_items.append(item)
                return
            
            # Проверяем тип выбранного элемента
            if chosen in self.groups:
                process_group(chosen)
            else:
                for item in self.items:
                    if item.layer.name == chosen and item.layer.group == group_name and item.visible:
                        visible_items.append(item)
        
        # Обрабатываем корневые группы
        root_groups = [name for name, g in self.groups.items() if not g.parent]
        for group_name in root_groups:
            process_group(group_name)
        
        # Добавляем элементы без групп
        for item in self.items:
            if not item.layer.group and item.visible:
                visible_items.append(item)
        
        return visible_items
    
    def _get_current_state_for_group(self, group_name: str) -> Optional[str]:
        """Определяет текущее состояние для группы"""
        group = self.groups.get(group_name)
        if not group:
            return None
        
        logic = group.logic
        now = time.time()
        
        # Обработка моргания
        if self.test_mode_var.get() != "none":
            blink_freq = group.blink_freq
            
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
        
        # Обработка случайного эффекта
        if group.random_effect and self.random_effect_var.get():
            if group_name not in self.group_random_timers:
                self.group_random_timers[group_name] = now
                self.group_random_current[group_name] = None
            
            if now > self.group_random_timers.get(group_name, 0):
                children = group.children
                if children:
                    blink_layer = logic.get("blink", "")
                    open_layer = logic.get("open", "")
                    
                    available = []
                    for child_name in children:
                        if child_name in self.layers:
                            if child_name != blink_layer and child_name != open_layer:
                                available.append(child_name)
                        elif child_name in self.groups:
                            child_group = self.groups.get(child_name)
                            if child_group and child_group.random_effect:
                                available.append(child_name)
                    
                    if available:
                        chosen = random.choice(available)
                        self.group_random_current[group_name] = chosen
            
            interval = random.uniform(group.random_min, group.random_max)
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
        
        # Возвращаем целевой элемент
        if current_state in logic and logic[current_state]:
            return logic[current_state]
        elif "open" in logic and logic["open"]:
            return logic["open"]
        elif "normal" in logic and logic["normal"]:
            return logic["normal"]
        elif "silent" in logic and logic["silent"]:
            return logic["silent"]
        
        # Первый доступный слой
        for item in self.items:
            if item.layer.group == group_name and item.visible:
                return item.layer.name
        
        return None
    
    def _optimized_preview_loop(self):
        """Оптимизированный цикл превью"""
        try:
            now = time.time()
            
            # Автосохранение с увеличенным интервалом
            if now - self.last_autosave > self.autosave_interval:
                self._autosave()
                self.last_autosave = now
            
            # Определяем режим и уровень
            mode = self.test_mode_var.get()
            level = self.audio_level if mode == "microphone" else 0.0
            
            # Обновляем индикатор уровня
            if mode == "microphone":
                self.level_bar["value"] = level * 100
            
            # Перерисовываем канвас
            self.redraw_canvas(level, mode)
            
        except Exception as e:
            logger.error(f"Error in preview loop: {e}")
        finally:
            if self.winfo_exists():
                self.after(int(1000 / self.preview_fps), self._optimized_preview_loop)
    
    def _autosave(self):
        """Оптимизированное автосохранение"""
        if not self.model_dir:
            return
        
        try:
            # Собираем данные модели
            model_data = {
                "name": self.model_name,
                "width": self.width,
                "height": self.height,
                "layers": [layer.to_dict() for layer in self.layers.values()],
                "groups": [group.__dict__ for group in self.groups.values()]
            }
            
            # Сохраняем только если есть изменения
            temp_path = os.path.join(self.model_dir, "model.json.tmp")
            final_path = os.path.join(self.model_dir, "model.json")
            
            with open(temp_path, "w", encoding="utf-8") as f:
                json.dump(model_data, f, indent=2, ensure_ascii=False)
            
            # Атомарная замена файла
            if os.path.exists(final_path):
                os.replace(temp_path, final_path)
            else:
                os.rename(temp_path, final_path)
                
        except Exception as e:
            logger.error(f"Error autosaving: {e}")
    
    # Остальные методы остаются с оптимизациями
    def on_window_resize(self, event):
        self.redraw_canvas()
    
    def _update_model_name(self, event=None):
        self.model_name = self.model_name_var.get()
    
    def update_canvas_size(self, event=None):
        try:
            new_width = max(100, min(3000, self.canvas_width_var.get()))
            new_height = max(100, min(3000, self.canvas_height_var.get()))
            
            self.width = new_width
            self.height = new_height
            
            self.canvas_width_var.set(new_width)
            self.canvas_height_var.set(new_height)
            
            self.zoom_reset()
            
        except Exception as e:
            messagebox.showerror("Ошибка", f"Неверный размер холста: {e}")
    
    def zoom_in(self):
        self.zoom_level = min(self.max_zoom, self.zoom_level + self.zoom_step)
        self.redraw_canvas()
    
    def zoom_out(self):
        self.zoom_level = max(self.min_zoom, self.zoom_level - self.zoom_step)
        self.redraw_canvas()
    
    def zoom_reset(self):
        self.zoom_level = 1.0
        self.offset_x = 0
        self.offset_y = 0
        self.redraw_canvas()
    
    def on_canvas_zoom(self, event):
        if event.delta > 0 or event.num == 4:
            self.zoom_in()
        else:
            self.zoom_out()
    
    def on_canvas_pan_start(self, event):
        self.is_panning = True
        self.last_pan_x = event.x
        self.last_pan_y = event.y
        self.canvas.config(cursor="fleur")
    
    def on_canvas_pan_move(self, event):
        if self.is_panning:
            dx = event.x - self.last_pan_x
            dy = event.y - self.last_pan_y
            self.offset_x += dx
            self.offset_y += dy
            self.last_pan_x = event.x
            self.last_pan_y = event.y
            self.redraw_canvas()
    
    def on_canvas_pan_end(self, event):
        self.is_panning = False
        self.canvas.config(cursor="crosshair")
    
    def update_test_mode(self):
        mode = self.test_mode_var.get()
        
        if mode == "none":
            self.level_frame.pack_forget()
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
        else:
            self.audio_processor.stop()
            if mode == "none":
                self.audio_level = 0.0
                self.level_bar["value"] = 0
    
    def on_audio_level(self, level):
        self.audio_level = level
    
    def new_model(self):
        name = simpledialog.askstring("Имя модели", "Введите имя модели", parent=self)
        if not name:
            return
        
        # Сбрасываем состояние
        self.model_name = name
        self.width = 700
        self.height = 700
        
        self.layers.clear()
        self.groups.clear()
        self.items.clear()
        self.imported_files.clear()
        
        self.model_name_var.set(name)
        self.canvas_width_var.set(700)
        self.canvas_height_var.set(700)
        
        self.tree_state["expanded_groups"].clear()
        self.tree_state["selected_items"].clear()
        self.tree_state["preserve_selection"] = False
        
        self.refresh_import_list()
        self.refresh_tree()
        self.zoom_reset()
        self.redraw_canvas()
        
        self._save_to_history("Новая модель")
    
    def load_model(self):
        # Диалог выбора слота
        slot_dialog = tk.Toplevel(self)
        slot_dialog.title("Загрузка из слота")
        slot_dialog.geometry("300x200")
        slot_dialog.transient(self)
        slot_dialog.grab_set()
        
        ttk.Label(slot_dialog, text="Выберите слот для загрузки:").pack(pady=10)
        
        for i in range(1, 7):
            slot_dir = os.path.join(MODELS_DIR, f"slot{i}")
            json_path = os.path.join(slot_dir, "model.json")
            
            btn_text = f"Слот {i} (есть модель)" if os.path.exists(json_path) else f"Слот {i} (пустой)"
            
            ttk.Button(
                slot_dialog,
                text=btn_text,
                command=lambda i=i: self._load_slot(i, slot_dialog)
            ).pack(fill="x", padx=20, pady=2)
    
    def _load_slot(self, slot_num, dialog):
        dialog.destroy()
        
        path = os.path.join(MODELS_DIR, f"slot{slot_num}")
        json_path = os.path.join(path, "model.json")
        
        if not os.path.exists(json_path):
            messagebox.showerror("Ошибка", "model.json не найден в выбранном слоте")
            return
        
        try:
            # Загружаем модель
            with open(json_path, "r", encoding="utf-8") as f:
                model_data = json.load(f)
            
            # Создаем временную папку
            temp_dir = os.path.join(MODELS_DIR, f"temp_{int(time.time())}_slot{slot_num}")
            os.makedirs(temp_dir, exist_ok=True)
            
            # Копируем файлы
            for fname in os.listdir(path):
                src = os.path.join(path, fname)
                if os.path.isfile(src):
                    shutil.copy2(src, os.path.join(temp_dir, fname))
            
            self.model_dir = temp_dir
            self.original_slot = slot_num
            
            # Загружаем данные модели
            self.model_name = model_data.get("name", "Без названия")
            self.width = model_data.get("width", 700)
            self.height = model_data.get("height", 700)
            
            # Загружаем слои - исправляем здесь
            self.layers.clear()
            for layer_data in model_data.get("layers", []):
                # Фильтруем только ожидаемые поля для LayerData
                valid_layer_data = {}
                valid_fields = ["name", "file", "x", "y", "scale", "rotation", 
                            "flip_horizontal", "flip_vertical", "visible", 
                            "is_gif", "group"]
                
                for field in valid_fields:
                    if field in layer_data:
                        valid_layer_data[field] = layer_data[field]
                
                # Переименовываем поля для соответствия LayerData
                if "flip_horizontal" in valid_layer_data:
                    valid_layer_data["flip_h"] = valid_layer_data.pop("flip_horizontal")
                if "flip_vertical" in valid_layer_data:
                    valid_layer_data["flip_v"] = valid_layer_data.pop("flip_vertical")
                
                layer = LayerData(**valid_layer_data)
                self.layers[layer.name] = layer
            
            # Загружаем группы - исправляем здесь
            self.groups.clear()
            for group_data in model_data.get("groups", []):
                # Фильтруем только ожидаемые поля для GroupData
                valid_group_data = {}
                valid_fields = ["name", "children", "parent", "logic", 
                            "blink_freq", "random_effect", "random_min", "random_max"]
                
                for field in valid_fields:
                    if field in group_data:
                        valid_group_data[field] = group_data[field]
                
                # Убедимся, что logic - это словарь
                if "logic" not in valid_group_data:
                    valid_group_data["logic"] = {}
                
                group = GroupData(**valid_group_data)
                self.groups[group.name] = group
            
            # Загружаем импортированные файлы
            self.imported_files.clear()
            for layer in self.layers.values():
                filepath = os.path.join(temp_dir, layer.file)
                if os.path.exists(filepath):
                    try:
                        with Image.open(filepath) as img:
                            is_gif = img.format == "GIF" and img.is_animated
                            img.seek(0)
                            preview_img = img.copy().convert("RGBA")
                            preview_img.thumbnail((50, 50))
                        self.imported_files.append((layer.file, preview_img, is_gif))
                    except Exception as e:
                        logger.error(f"Error loading preview: {e}")
            
            # Обновляем UI
            self.model_name_var.set(self.model_name)
            self.canvas_width_var.set(self.width)
            self.canvas_height_var.set(self.height)
            
            # Перестраиваем items
            self._rebuild_items_from_layers()
            
            # Обновляем интерфейс
            self.refresh_import_list()
            
            self.tree_state["expanded_groups"].clear()
            self.tree_state["selected_items"].clear()
            self.tree_state["preserve_selection"] = False
            
            self.refresh_tree()
            self.zoom_reset()
            self.redraw_canvas()
            
            # Очищаем старые папки
            self.cleanup_old_temp_folders()
            
            self._save_to_history(f"Загрузка из слота {slot_num}")
            
            logger.info(f"Model loaded from slot {slot_num}")
            
        except Exception as e:
            logger.error(f"Error loading model: {e}")
            messagebox.showerror("Ошибка", f"Не удалось загрузить модель: {e}")
    
    def save_model(self):
        """Сохранение модели"""
        if not self.model_dir:
            # Создаем временную папку
            tmp = os.path.join(MODELS_DIR, f"model_temp_{int(time.time())}")
            os.makedirs(tmp, exist_ok=True)
            self.model_dir = tmp
            
            self.cleanup_old_temp_folders()
        
        # Сохраняем имя и размеры
        self.model_name = self.model_name_var.get()
        
        # Собираем данные модели
        model_data = {
            "name": self.model_name,
            "width": self.width,
            "height": self.height,
            "layers": [layer.to_dict() for layer in self.layers.values()],
            "groups": [group.__dict__ for group in self.groups.values()]
        }
        
        # Сохраняем JSON
        model_json_path = os.path.join(self.model_dir, "model.json")
        try:
            with open(model_json_path, "w", encoding="utf-8") as f:
                json.dump(model_data, f, indent=2, ensure_ascii=False)
        except Exception as e:
            logger.error(f"Error saving model JSON: {e}")
            messagebox.showerror("Ошибка", f"Не удалось сохранить модель: {e}")
            return
        
        # Создаем превью
        self._create_preview()
        
        # Показываем диалог выбора слота
        self._show_save_slot_dialog()
        
        self.last_autosave = time.time()
        self._save_to_history("Сохранение модели")
    
    def _show_save_slot_dialog(self):
        """Показывает диалог выбора слота для сохранения"""
        slot_dialog = tk.Toplevel(self)
        slot_dialog.title("Сохранение в слот")
        slot_dialog.geometry("300x250")
        slot_dialog.transient(self)
        slot_dialog.grab_set()
        
        ttk.Label(slot_dialog, text="Выберите слот для сохранения:").pack(pady=10)
        
        slots_frame = ttk.Frame(slot_dialog)
        slots_frame.pack(fill="both", expand=True, padx=10, pady=5)
        
        for i in range(1, 7):
            slot_dir = os.path.join(MODELS_DIR, f"slot{i}")
            json_path = os.path.join(slot_dir, "model.json")
            
            btn_text = f"Слот {i} (перезаписать)" if os.path.exists(json_path) else f"Слот {i} (новый)"
            
            btn = ttk.Button(
                slots_frame,
                text=btn_text,
                width=20,
                command=lambda i=i: self._save_to_slot(i, slot_dialog)
            )
            btn.pack(fill="x", padx=10, pady=3)
        
        ttk.Button(
            slot_dialog,
            text="Отмена",
            command=slot_dialog.destroy
        ).pack(fill="x", padx=20, pady=10)
    
    def _save_to_slot(self, slot_num, dialog):
        """Сохраняет модель в слот"""
        dialog.destroy()
        
        slot_dir = os.path.join(MODELS_DIR, f"slot{slot_num}")
        os.makedirs(slot_dir, exist_ok=True)
        
        try:
            # Очищаем слот
            for fname in os.listdir(slot_dir):
                file_path = os.path.join(slot_dir, fname)
                try:
                    if os.path.isfile(file_path):
                        os.unlink(file_path)
                except Exception as e:
                    logger.error(f"Error deleting file: {e}")
            
            # Копируем файлы
            for fname in os.listdir(self.model_dir):
                src = os.path.join(self.model_dir, fname)
                if os.path.isfile(src):
                    shutil.copy2(src, os.path.join(slot_dir, fname))
            
            # Обновляем текущий слот
            self.original_slot = slot_num
            
            # Вызываем callback
            if self.on_save:
                # Создаем данные модели для callback
                model_data = {
                    "name": self.model_name,
                    "width": self.width,
                    "height": self.height,
                    "layers": [layer.to_dict() for layer in self.layers.values()],
                    "groups": [group.__dict__ for group in self.groups.values()]
                }
                self.on_save(model_data, slot_dir, slot_num)
            
            messagebox.showinfo("Сохранено", f"Модель сохранена в слот {slot_num}")
            
            logger.info(f"Model saved to slot {slot_num}")
            
        except Exception as e:
            logger.error(f"Error saving to slot: {e}")
            messagebox.showerror("Ошибка", f"Не удалось сохранить модель: {e}")
    
    def _create_preview(self):
        """Создает превью модели"""
        if not self.model_dir:
            return
        
        # Создаем базовое изображение
        base = Image.new("RGBA", (self.width, self.height), (0, 0, 0, 0))
        center_x = self.width // 2
        center_y = self.height // 2
        
        # Используем видимые элементы
        visible_items = self._get_visible_items_cached()
        
        for item in visible_items:
            img = item.get_current_image()
            if not img:
                continue
            
            px = center_x - img.size[0] // 2 + int(item.x)
            py = center_y - img.size[1] // 2 + int(item.y)
            
            try:
                base.alpha_composite(img, (px, py))
            except Exception as e:
                logger.error(f"Error creating preview: {e}")
        
        # Создаем миниатюру
        base.thumbnail((200, 200))
        preview_path = os.path.join(self.model_dir, "preview.png")
        base.save(preview_path)
    
    def import_images(self):
        """Импорт изображений"""
        files = filedialog.askopenfilenames(
            title="Выберите PNG или GIF изображения",
            filetypes=[("Изображения", "*.png *.gif"), ("Все файлы", "*.*")]
        )
        
        if not files:
            return
        
        # Создаем временную папку если нужно
        if not self.model_dir:
            tmp = os.path.join(MODELS_DIR, f"model_temp_{int(time.time())}")
            os.makedirs(tmp, exist_ok=True)
            self.model_dir = tmp
            
            self.cleanup_old_temp_folders()
        
        # Обрабатываем файлы
        imported_count = 0
        for filepath in files:
            try:
                filename = os.path.basename(filepath)
                dest = os.path.join(self.model_dir, filename)
                
                # Копируем файл
                if os.path.abspath(filepath) != os.path.abspath(dest):
                    shutil.copy2(filepath, dest)
                
                # Определяем тип
                is_gif = filename.lower().endswith('.gif')
                
                # Создаем превью
                with Image.open(filepath) as img:
                    if is_gif:
                        is_gif = img.is_animated
                    img.seek(0)
                    preview_img = img.copy().convert("RGBA")
                    preview_img.thumbnail((50, 50))
                
                # Создаем слой
                layer_name = os.path.splitext(filename)[0]
                layer = LayerData(
                    name=layer_name,
                    file=filename,
                    is_gif=is_gif
                )
                
                # Добавляем в структуры
                self.layers[layer_name] = layer
                self.imported_files.append((filename, preview_img, is_gif))
                
                # Создаем элемент канваса
                item = OptimizedCanvasItem(layer, dest)
                self.items.append(item)
                
                imported_count += 1
                
            except Exception as e:
                logger.error(f"Error importing image {filepath}: {e}")
        
        # Обновляем интерфейс
        if imported_count > 0:
            self.refresh_import_list()
            self.refresh_tree()
            self.redraw_canvas()
            
            self._save_to_history(f"Импорт {imported_count} изображений")
            
            logger.info(f"Imported {imported_count} images")
    
    def refresh_import_list(self):
        """Обновляет список импортированных изображений"""
        for widget in self.import_inner.winfo_children():
            widget.destroy()
        
        for filename, preview_img, is_gif in self.imported_files:
            row = ttk.Frame(self.import_inner)
            row.pack(fill="x", padx=2, pady=2)
            
            icon = "GIF" if is_gif else "PNG"
            
            ttk.Label(row, text=f"{icon}: {filename}", width=20).pack(side="left", padx=2)
            
            ttk.Button(row, text="+", width=2, 
                      command=lambda f=filename: self.add_to_canvas(f)).pack(side="left", padx=2)
            
            ttk.Button(row, text="-", width=2,
                      command=lambda f=filename: self.remove_from_canvas_by_file(f)).pack(side="left", padx=2)
            
            ttk.Button(row, text="🗑️", width=2,
                      command=lambda f=filename: self.delete_file(f)).pack(side="left", padx=2)
    
    def add_to_canvas(self, filename):
        """Добавляет изображение на канвас"""
        for fname, _, is_gif in self.imported_files:
            if fname == filename:
                # Ищем существующий слой или создаем новый
                layer_name = os.path.splitext(filename)[0]
                
                if layer_name not in self.layers:
                    layer = LayerData(
                        name=layer_name,
                        file=filename,
                        is_gif=is_gif
                    )
                    self.layers[layer_name] = layer
                
                # Создаем элемент если его нет
                filepath = os.path.join(self.model_dir, filename)
                if os.path.exists(filepath):
                    # Проверяем, есть ли уже такой элемент
                    existing = False
                    for item in self.items:
                        if item.layer.name == layer_name:
                            existing = True
                            break
                    
                    if not existing:
                        item = OptimizedCanvasItem(self.layers[layer_name], filepath)
                        self.items.append(item)
                
                self.refresh_tree()
                self.redraw_canvas()
                break
    
    def remove_from_canvas_by_file(self, filename):
        """Удаляет элемент с канваса по имени файла"""
        layer_name = os.path.splitext(filename)[0]
        
        # Удаляем элементы
        self.items = [item for item in self.items if item.layer.file != filename]
        
        # Удаляем слой если есть
        if layer_name in self.layers:
            del self.layers[layer_name]
        
        self.refresh_tree()
        self.redraw_canvas()
    
    def delete_file(self, filename):
        """Удаляет файл"""
        if messagebox.askyesno("Удаление файла", f"Удалить {filename} навсегда?"):
            # Удаляем с канваса
            self.remove_from_canvas_by_file(filename)
            
            # Удаляем из списка импортированных
            self.imported_files = [f for f in self.imported_files if f[0] != filename]
            
            # Удаляем файл
            if self.model_dir:
                file_path = os.path.join(self.model_dir, filename)
                if os.path.exists(file_path):
                    os.remove(file_path)
            
            self.refresh_import_list()
            self.refresh_tree()
            self.redraw_canvas()
            
            self._save_to_history(f"Удаление файла {filename}")
    
    def refresh_tree(self):
        """Обновляет древовидный список"""
        try:
            # Сохраняем состояние
            self._save_tree_state()
            
            # Очищаем дерево
            self.tree.delete(*self.tree.get_children())
            
            # Создаем узлы групп
            group_nodes = {}
            
            # Функция для создания узла группы
            def create_group_node(group_name, parent_node=""):
                group = self.groups.get(group_name)
                if not group:
                    return None
                
                # Создаем узел
                node = self.tree.insert(parent_node, "end", 
                                       text=f"📁 {group_name}",
                                       values=("group", group_name))
                group_nodes[group_name] = node
                
                # Добавляем дочерние элементы
                for child_name in group.children:
                    if child_name in self.layers:
                        # Это слой
                        item = next((i for i in self.items if i.layer.name == child_name), None)
                        if item:
                            self.tree.insert(node, "end",
                                           text=self._get_item_display_text(item),
                                           values=("item", id(item)))
                    elif child_name in self.groups:
                        # Это вложенная группа
                        create_group_node(child_name, node)
                
                return node
            
            # Создаем корневые группы
            root_groups = [name for name, g in self.groups.items() if not g.parent]
            for group_name in root_groups:
                create_group_node(group_name)
            
            # Добавляем элементы без групп
            for item in self.items:
                if not item.layer.group:
                    self.tree.insert("", "end",
                                   text=self._get_item_display_text(item),
                                   values=("item", id(item)))
            
            # Восстанавливаем состояние
            self._restore_tree_state()
            
        except Exception as e:
            logger.error(f"Error refreshing tree: {e}")
    
    def _get_item_display_text(self, item: OptimizedCanvasItem) -> str:
        """Получает текст для отображения элемента"""
        name = item.layer.name
        flags = []
        
        if not item.visible:
            flags.append("✘")
        else:
            flags.append("✔")
        
        if item.is_gif:
            flags.append("GIF")
        
        if item.flip_h:
            flags.append("зерк.гор")
        
        if item.flip_v:
            flags.append("зерк.верт")
        
        if item.rotation != 0:
            flags.append(f"↻{item.rotation}°")
        
        if item.scale != 1.0:
            flags.append(f"⤢{item.scale}")
        
        flag_text = f" ({', '.join(flags)})" if flags else ""
        return f"{name}{flag_text}"
    
    def _save_tree_state(self):
        """Сохраняет состояние дерева"""
        self.tree_state["expanded_groups"].clear()
        self.tree_state["selected_items"].clear()
        
        # Сохраняем раскрытые группы
        for item in self.tree.get_children():
            if self.tree.item(item, "open"):
                values = self.tree.item(item, "values")
                if values and values[0] == "group":
                    self.tree_state["expanded_groups"].add(values[1])
        
        # Сохраняем выделенные элементы
        for item in self.tree.selection():
            values = self.tree.item(item, "values")
            if values:
                self.tree_state["selected_items"].add(tuple(values))
    
    def _restore_tree_state(self):
        """Восстанавливает состояние дерева"""
        # Раскрываем группы
        for item in self.tree.get_children():
            values = self.tree.item(item, "values")
            if values and values[0] == "group" and values[1] in self.tree_state["expanded_groups"]:
                self.tree.item(item, open=True)
        
        # Восстанавливаем выделение
        if self.tree_state["preserve_selection"]:
            for item in self.tree.get_children():
                values = self.tree.item(item, "values")
                if values and tuple(values) in self.tree_state["selected_items"]:
                    self.tree.selection_add(item)
                
                # Проверяем дочерние элементы
                if values and values[0] == "group":
                    for child in self.tree.get_children(item):
                        child_values = self.tree.item(child, "values")
                        if child_values and tuple(child_values) in self.tree_state["selected_items"]:
                            self.tree.selection_add(child)
            
            self.tree_state["preserve_selection"] = False
    
    def on_tree_select(self, event=None):
        """Обработка выбора в дереве"""
        try:
            # Если фокус в поле ввода, пропускаем
            focus_widget = self.focus_get()
            if isinstance(focus_widget, (ttk.Entry, tk.Entry, ttk.Combobox)):
                return
            
            selection = self.tree.selection()
            self.current_selection = []
            self.selected_group = None
            
            # Снимаем выделение
            for item in self.items:
                item._selected = False
            
            if not selection:
                self.group_label.config(text="(нет группы)")
                self.clear_props_fields()
                self.redraw_canvas()
                return
            
            selected_groups = set()
            selected_items = set()
            
            for item_id in selection:
                item_values = self.tree.item(item_id, "values")
                if not item_values or len(item_values) < 2:
                    continue
                
                item_type, item_data = item_values
                
                if item_type == "group":
                    # Выбрана группа
                    group_name = item_data
                    selected_groups.add(group_name)
                    
                    # Выбираем все элементы группы
                    for item in self.items:
                        if item.layer.group == group_name:
                            item._selected = True
                            selected_items.add(item)
                    
                    # Рекурсивно выбираем вложенные элементы
                    def select_subgroups(group_name):
                        for group in self.groups.values():
                            if group.parent == group_name:
                                for item in self.items:
                                    if item.layer.group == group.name:
                                        item._selected = True
                                        selected_items.add(item)
                                select_subgroups(group.name)
                    
                    select_subgroups(group_name)
                    
                elif item_type == "item":
                    # Выбран элемент
                    item_id_int = int(item_data)
                    item = next((i for i in self.items if id(i) == item_id_int), None)
                    if item:
                        item._selected = True
                        selected_items.add(item)
            
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
            
            self.redraw_canvas()
            
        except Exception as e:
            logger.error(f"Error in on_tree_select: {e}")
    
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
    
    def load_item_props(self, item: OptimizedCanvasItem):
        """Загружает свойства элемента"""
        try:
            self.name_entry.delete(0, "end")
            self.name_entry.insert(0, item.layer.name)
            
            self.x_entry.delete(0, "end")
            self.x_entry.insert(0, str(item.x))
            
            self.y_entry.delete(0, "end")
            self.y_entry.insert(0, str(item.y))
            
            self.scale_entry.delete(0, "end")
            self.scale_entry.insert(0, str(item.scale))
            
            self.rotation_entry.delete(0, "end")
            self.rotation_entry.insert(0, str(item.rotation))
            
            self.flip_h_var.set(item.flip_h)
            self.flip_v_var.set(item.flip_v)
            self.visible_var.set(item.visible)
            
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
        
        try:
            # Читаем значения
            name = self.name_entry.get().strip()
            x = int(self.x_entry.get().strip())
            y = int(self.y_entry.get().strip())
            scale = float(self.scale_entry.get().strip())
            rotation = int(self.rotation_entry.get().strip())
            visible = self.visible_var.get()
            flip_h = self.flip_h_var.get()
            flip_v = self.flip_v_var.get()
            
            need_redraw = False
            
            # Применяем к каждому выбранному элементу
            for item in self.current_selection:
                if name and item.layer.name != name:
                    # Обновляем имя слоя
                    old_name = item.layer.name
                    item.layer.name = name
                    self.layers[name] = item.layer
                    if old_name in self.layers:
                        del self.layers[old_name]
                
                item.x = x
                item.y = y
                item.visible = visible
                
                # Проверяем изменения трансформаций
                if (scale != item.scale or rotation != item.rotation or 
                    flip_h != item.flip_h or flip_v != item.flip_v):
                    
                    item.scale = scale
                    item.rotation = rotation
                    item.flip_h = flip_h
                    item.flip_v = flip_v
                    
                    item.update_transformed_image()
                    need_redraw = True
            
            if need_redraw:
                self.redraw_canvas()
            
            # Сохраняем состояние дерева
            self._save_tree_state()
            self.tree_state["preserve_selection"] = True
            
            self.refresh_tree()
            self._save_to_history("Изменение свойств")
            
        except Exception as e:
            messagebox.showwarning("Ошибка", "Проверьте правильность введенных значений")
            logger.error(f"Error applying properties: {e}")
    
    def on_mirror_change(self):
        """Обработка изменения зеркалирования"""
        if not self.current_selection:
            return
        
        for item in self.current_selection:
            item.flip_h = self.flip_h_var.get()
            item.flip_v = self.flip_v_var.get()
            item.update_transformed_image()
        
        self.redraw_canvas()
    
    def group_selected(self):
        """Создает группу из выбранных элементов"""
        if not self.current_selection:
            messagebox.showwarning("Группа", "Выберите хотя бы один элемент")
            return
        
        name = simpledialog.askstring("Имя группы", "Введите имя новой группы", parent=self)
        if not name:
            return
        
        if name in self.groups:
            messagebox.showwarning("Группа", "Имя группы уже существует")
            return
        
        # Определяем родительскую группу
        parent_group = None
        selected_groups = set()
        
        for item in self.current_selection:
            if item.layer.group:
                selected_groups.add(item.layer.group)
        
        if len(selected_groups) == 1:
            parent_group = list(selected_groups)[0]
        elif self.selected_group:
            parent_group = self.selected_group
        
        # Создаем группу
        group = GroupData(
            name=name,
            children=[item.layer.name for item in self.current_selection],
            parent=parent_group
        )
        
        self.groups[name] = group
        
        # Устанавливаем группу для элементов
        for item in self.current_selection:
            item.layer.group = name
        
        # Обновляем родительскую группу
        if parent_group and parent_group in self.groups:
            parent = self.groups[parent_group]
            # Удаляем элементы из родительской группы
            for item in self.current_selection:
                if item.layer.name in parent.children:
                    parent.children.remove(item.layer.name)
            # Добавляем новую группу
            parent.children.append(name)
        
        # Сбрасываем выделение
        for item in self.items:
            item._selected = False
        
        self.current_selection = []
        self.selected_group = name
        
        self.refresh_tree()
        self.redraw_canvas()
        
        self._save_to_history(f"Создание группы {name}")
    
    def ungroup_selected(self):
        """Разгруппирует выбранные элементы"""
        if self.selected_group:
            group_name = self.selected_group
            group = self.groups.get(group_name)
            
            if not group:
                return
            
            parent_group = group.parent
            
            # Переносим элементы в родительскую группу или убираем группу
            for item in self.items:
                if item.layer.group == group_name:
                    if parent_group:
                        item.layer.group = parent_group
                    else:
                        item.layer.group = None
            
            # Удаляем группу
            del self.groups[group_name]
            
            # Обновляем родительскую группу
            if parent_group and parent_group in self.groups:
                parent = self.groups[parent_group]
                if group_name in parent.children:
                    parent.children.remove(group_name)
            
            self.selected_group = parent_group
            
            self.refresh_tree()
            self.redraw_canvas()
            
            self._save_to_history(f"Разгруппирование {group_name}")
            return
        
        if not self.current_selection:
            return
        
        # Разгруппирование отдельных элементов
        for item in self.current_selection:
            group_name = item.layer.group
            if group_name and group_name in self.groups:
                group = self.groups[group_name]
                if item.layer.name in group.children:
                    group.children.remove(item.layer.name)
                
                # Удаляем группу если она пуста
                if not group.children:
                    del self.groups[group_name]
            
            item.layer.group = None
        
        self.refresh_tree()
        self.redraw_canvas()
        
        self._save_to_history("Разгруппирование элементов")
    
    def bring_forward(self):
        """Перемещает элементы вперед"""
        if not self.current_selection:
            return
        
        # Сортируем по индексу в обратном порядке
        for item in sorted(self.current_selection, key=lambda x: self.items.index(x), reverse=True):
            idx = self.items.index(item)
            if idx < len(self.items) - 1:
                # Меняем местами
                self.items[idx], self.items[idx + 1] = self.items[idx + 1], self.items[idx]
        
        self.refresh_tree()
        self.redraw_canvas()
        
        self._save_to_history("Перемещение вперед")
    
    def send_backward(self):
        """Перемещает элементы назад"""
        if not self.current_selection:
            return
        
        # Сортируем по индексу
        for item in sorted(self.current_selection, key=lambda x: self.items.index(x)):
            idx = self.items.index(item)
            if idx > 0:
                # Меняем местами
                self.items[idx], self.items[idx - 1] = self.items[idx - 1], self.items[idx]
        
        self.refresh_tree()
        self.redraw_canvas()
        
        self._save_to_history("Перемещение назад")
    
    def load_group_settings(self, group_name: str):
        """Загружает настройки группы"""
        group = self.groups.get(group_name)
        if not group:
            return
        
        # Обновляем меню
        self.update_group_logic_menus(group_name)
        
        # Загружаем логику
        logic = group.logic
        for state in ["silent", "whisper", "normal", "shout", "blink", "open"]:
            self.state_vars[state].set(logic.get(state, ""))
        
        # Загружаем моргание
        self.blink_freq.set(group.blink_freq)
        
        # Загружаем случайный эффект
        self.random_effect_var.set(group.random_effect)
        self.random_min_var.set(group.random_min)
        self.random_max_var.set(group.random_max)
    
    def update_group_logic_menus(self, group_name: str):
        """Обновляет меню логики группы"""
        group = self.groups.get(group_name)
        if not group:
            return
        
        # Собираем опции
        options = [""]
        
        # Слои в группе
        for item in self.items:
            if item.layer.group == group_name:
                options.append(item.layer.name)
        
        # Дочерние группы
        for g in self.groups.values():
            if g.parent == group_name:
                options.append(g.name)
        
        # Обновляем меню
        for state in ["silent", "whisper", "normal", "shout", "blink", "open"]:
            menu_widget = getattr(self, f"{state}_menu")
            menu = menu_widget['menu']
            menu.delete(0, 'end')
            
            var = self.state_vars[state]
            
            for option in options:
                menu.add_command(
                    label=option,
                    command=lambda val=option, v=var: v.set(val)
                )
    
    def apply_group_logic(self):
        """Применяет логику группы"""
        if not self.selected_group:
            messagebox.showwarning("Нет группы", "Сначала выберите группу")
            return
        
        group_name = self.selected_group
        group = self.groups.get(group_name)
        
        if not group:
            messagebox.showerror("Ошибка", "Группа не найдена")
            return
        
        # Сохраняем логику
        logic = {}
        for state, var in self.state_vars.items():
            val = var.get().strip()
            if val:
                logic[state] = val
        
        group.logic = logic
        
        # Сохраняем моргание
        try:
            blink_freq_value = self.blink_freq.get()
            if blink_freq_value == "" or blink_freq_value is None:
                blink_freq_value = 0.0
            group.blink_freq = float(blink_freq_value)
        except Exception as e:
            logger.error(f"Error converting blink_freq: {e}")
            group.blink_freq = 0.0
        
        # Сохраняем случайный эффект
        group.random_effect = self.random_effect_var.get()
        group.random_min = self.random_min_var.get()
        group.random_max = self.random_max_var.get()
        
        # Сохраняем состояние дерева
        self._save_tree_state()
        self.tree_state["preserve_selection"] = True
        
        messagebox.showinfo("Логика группы", f"Логика для группы {group_name} сохранена")
        
        self._save_to_history(f"Изменение логики группы {group_name}")
    
    def on_canvas_mouse_down(self, event):
        """Обработка нажатия мыши на канвасе"""
        # Пропускаем если фокус в поле ввода
        focus_widget = self.focus_get()
        if focus_widget and isinstance(focus_widget, (ttk.Entry, tk.Entry, ttk.Combobox)):
            return
        
        # Преобразуем координаты
        canvas_width = self.canvas.winfo_width()
        canvas_height = self.canvas.winfo_height()
        
        scaled_width = self.width * self.zoom_level
        scaled_height = self.height * self.zoom_level
        
        center_x = canvas_width // 2 + self.offset_x
        center_y = canvas_height // 2 + self.offset_y
        
        canvas_x1 = center_x - scaled_width // 2
        canvas_y1 = center_y - scaled_height // 2
        
        mx = event.x - canvas_x1
        my = event.y - canvas_y1
        
        if self.zoom_level > 0:
            mx = mx / self.zoom_level
            my = my / self.zoom_level
        
        # Сбрасываем выделение
        for item in self.items:
            item._selected = False
        
        self.current_selection = []
        self.drag_data = {"item": None, "x": mx, "y": my, "group_items": []}
        
        found = None
        
        # Ищем элемент под курсором
        for item in reversed(self.items):
            if not item.visible:
                continue
            
            img = item.get_current_image()
            if not img:
                continue
            
            # Рассчитываем позицию
            px = (self.width // 2) - img.width // 2 + item.x
            py = (self.height // 2) - img.height // 2 + item.y
            
            # Проверяем bounding box
            if px <= mx <= px + img.width and py <= my <= py + img.height:
                # Проверяем прозрачность
                try:
                    if img.mode == 'RGBA':
                        pixel_x = int(mx - px)
                        pixel_y = int(my - py)
                        
                        if 0 <= pixel_x < img.width and 0 <= pixel_y < img.height:
                            pixel = img.getpixel((pixel_x, pixel_y))
                            if len(pixel) >= 4 and pixel[3] > 0:
                                found = item
                                break
                    else:
                        found = item
                        break
                except Exception as e:
                    logger.error(f"Error checking pixel: {e}")
                    found = item
                    break
        
        if found:
            # Ctrl для инвертирования выделения
            if event.state & 0x0004:
                found._selected = not found._selected
                if found._selected:
                    self.current_selection.append(found)
                else:
                    if found in self.current_selection:
                        self.current_selection.remove(found)
            else:
                # Обычное выделение
                for item in self.items:
                    item._selected = False
                
                found._selected = True
                self.current_selection = [found]
            
            # Сохраняем для перемещения
            if self.selected_group:
                # Собираем все элементы группы
                self.drag_data["group_items"] = [
                    item for item in self.items 
                    if item.layer.group == self.selected_group
                ]
            else:
                self.drag_data["group_items"] = self.current_selection.copy()
            
            self.drag_data["item"] = found
            
            # Сохраняем состояние дерева
            self._save_tree_state()
            self.tree_state["preserve_selection"] = True
            
            self.refresh_tree()
        else:
            # Клик вне элементов
            for item in self.items:
                item._selected = False
            
            self.current_selection = []
            self.selected_group = None
            self.refresh_tree()
    
    def on_canvas_mouse_move(self, event):
        """Обработка перемещения мыши"""
        if not self.drag_data.get("item") or not self.drag_data.get("group_items"):
            return
        
        # Преобразуем координаты
        canvas_width = self.canvas.winfo_width()
        canvas_height = self.canvas.winfo_height()
        
        scaled_width = self.width * self.zoom_level
        scaled_height = self.height * self.zoom_level
        
        center_x = canvas_width // 2 + self.offset_x
        center_y = canvas_height // 2 + self.offset_y
        
        canvas_x1 = center_x - scaled_width // 2
        canvas_y1 = center_y - scaled_height // 2
        
        mx = event.x - canvas_x1
        my = event.y - canvas_y1
        
        if self.zoom_level > 0:
            mx = mx / self.zoom_level
            my = my / self.zoom_level
        
        dx = mx - self.drag_data["x"]
        dy = my - self.drag_data["y"]
        
        self.drag_data["x"] = mx
        self.drag_data["y"] = my
        
        # Перемещаем элементы
        for item in self.drag_data["group_items"]:
            item.x += int(dx)
            item.y += int(dy)
        
        # Обновляем поля координат
        if len(self.current_selection) == 1:
            self.x_entry.delete(0, "end")
            self.x_entry.insert(0, str(self.current_selection[0].x))
            
            self.y_entry.delete(0, "end")
            self.y_entry.insert(0, str(self.current_selection[0].y))
        
        self.redraw_canvas()
    
    def on_canvas_mouse_up(self, event):
        """Обработка отпускания мыши"""
        self.drag_data["item"] = None
        self._save_to_history("Перемещение элемента")
    
    def show_blink_preview(self):
        """Показывает превью моргания"""
        if not self.selected_group:
            return
        
        group_name = self.selected_group
        group = self.groups.get(group_name)
        
        if not group:
            return
        
        blink_freq = self.blink_freq.get()
        if blink_freq < 0.1:
            return
        
        self.blink_preview_running = True
        self._blink_preview_loop()
    
    def stop_blink_preview(self):
        """Останавливает превью моргания"""
        self.blink_preview_running = False
    
    def _blink_preview_loop(self):
        """Цикл превью моргания"""
        if not self.blink_preview_running:
            return
        
        group_name = self.selected_group
        group = self.groups.get(group_name)
        
        if not group:
            return
        
        logic = group.logic
        blink_freq = group.blink_freq
        
        if blink_freq < 0.1:
            return
        
        blink_layer = logic.get("blink", "")
        open_layer = logic.get("open") or logic.get("normal") or logic.get("whisper") or logic.get("silent")
        
        # Моргание
        for item in self.items:
            if item.layer.group == group_name:
                item.visible = (item.layer.name == blink_layer)
        
        self.redraw_canvas(0, "none")
        
        self.after(200, lambda: self._show_normal_preview(open_layer, group_name, blink_freq))
    
    def _show_normal_preview(self, open_layer: str, group_name: str, blink_freq: float):
        """Показывает нормальное состояние"""
        if not self.blink_preview_running:
            return
        
        for item in self.items:
            if item.layer.group == group_name:
                item.visible = (item.layer.name == open_layer)
        
        self.redraw_canvas(0, "none")
        
        if blink_freq > 0.1:
            self.after(int(blink_freq * 1000), self._blink_preview_loop)
    
    def export_zip(self):
        """Экспортирует модель в ZIP"""
        try:
            from utils import export_model_zip
        except Exception as e:
            messagebox.showerror("Ошибка экспорта", f"Не удалось импортировать утилиту экспорта: {e}")
            return
        
        if not self.layers:
            messagebox.showwarning("Нет модели", "Сначала создайте или загрузите модель")
            return
        
        default_name = f"{self.model_name.replace(' ', '_')}.zip"
        zip_path = filedialog.asksaveasfilename(
            title="Сохранить модель как ZIP",
            defaultextension=".zip",
            filetypes=[("ZIP архивы", "*.zip"), ("Все файлы", "*.*")],
            initialfile=default_name
        )
        
        if not zip_path:
            return
        
        try:
            # Создаем временную папку
            with tempfile.TemporaryDirectory(prefix="model_export_") as temp_dir:
                # Собираем данные модели
                model_data = {
                    "name": self.model_name,
                    "width": self.width,
                    "height": self.height,
                    "layers": [layer.to_dict() for layer in self.layers.values()],
                    "groups": [group.__dict__ for group in self.groups.values()]
                }
                
                # Копируем изображения
                for layer in self.layers.values():
                    if self.model_dir:
                        src = os.path.join(self.model_dir, layer.file)
                        if os.path.exists(src):
                            dst = os.path.join(temp_dir, layer.file)
                            shutil.copy2(src, dst)
                
                # Создаем превью
                self._create_preview_for_export(temp_dir)
                
                # Экспортируем
                export_model_zip(model_data, temp_dir, zip_path)
            
            messagebox.showinfo("Экспортировано", f"Модель экспортирована:\n{zip_path}")
            
        except Exception as e:
            import traceback
            tb = traceback.format_exc()
            
            with open("export_zip_error.log", "w", encoding="utf-8") as f:
                f.write(tb)
            
            messagebox.showerror("Ошибка экспорта", f"Ошибка при экспорте: {e}. Смотри export_zip_error.log")
            logger.error(f"Error exporting model: {e}\n{tb}")
    
    def _create_preview_for_export(self, temp_dir: str):
        """Создает превью для экспорта"""
        try:
            base = Image.new("RGBA", (self.width, self.height), (0, 0, 0, 0))
            center_x = self.width // 2
            center_y = self.height // 2
            
            visible_items = self._get_visible_items_cached()
            
            for item in visible_items:
                img = item.get_current_image()
                if not img:
                    continue
                
                px = center_x - img.size[0] // 2 + int(item.x)
                py = center_y - img.size[1] // 2 + int(item.y)
                
                try:
                    base.alpha_composite(img, (px, py))
                except Exception as e:
                    logger.error(f"Error creating preview: {e}")
            
            base.thumbnail((200, 200))
            preview_path = os.path.join(temp_dir, "preview.png")
            base.save(preview_path)
            
        except Exception as e:
            logger.error(f"Error creating preview for export: {e}")
    
    def import_zip(self):
        """Импортирует модель из ZIP"""
        try:
            from utils import import_model_zip
        except Exception as e:
            messagebox.showerror("Ошибка импорта", f"Не удалось импортировать утилиту импорта: {e}")
            return
        
        zip_path = filedialog.askopenfilename(
            title="Выберите ZIP архив с моделью",
            filetypes=[("ZIP архивы", "*.zip"), ("Все файлы", "*.*")]
        )
        
        if not zip_path:
            return
        
        try:
            # Импортируем
            model_data, import_dir = import_model_zip(zip_path)
            
            # Загружаем модель
            self.model_name = model_data.get("name", "Без названия")
            self.width = model_data.get("width", 700)
            self.height = model_data.get("height", 700)
            
            self.model_dir = import_dir
            self.original_slot = None
            
            # Загружаем слои
            self.layers.clear()
            for layer_data in model_data.get("layers", []):
                layer = LayerData(**layer_data)
                self.layers[layer.name] = layer
            
            # Загружаем группы
            self.groups.clear()
            for group_data in model_data.get("groups", []):
                group = GroupData(**group_data)
                self.groups[group.name] = group
            
            # Загружаем импортированные файлы
            self.imported_files.clear()
            for layer in self.layers.values():
                filepath = os.path.join(import_dir, layer.file)
                if os.path.exists(filepath):
                    try:
                        with Image.open(filepath) as img:
                            is_gif = img.format == "GIF" and img.is_animated
                            img.seek(0)
                            preview_img = img.copy().convert("RGBA")
                            preview_img.thumbnail((50, 50))
                        self.imported_files.append((layer.file, preview_img, is_gif))
                    except Exception as e:
                        logger.error(f"Error loading imported file: {e}")
            
            # Перестраиваем items
            self._rebuild_items_from_layers()
            
            # Обновляем UI
            self.model_name_var.set(self.model_name)
            self.canvas_width_var.set(self.width)
            self.canvas_height_var.set(self.height)
            
            self.refresh_import_list()
            
            self.tree_state["expanded_groups"].clear()
            self.tree_state["selected_items"].clear()
            self.tree_state["preserve_selection"] = False
            
            self.refresh_tree()
            self.zoom_reset()
            self.redraw_canvas()
            
            # Очищаем старые папки
            self.cleanup_old_temp_folders()
            
            messagebox.showinfo("Импорт завершен", f"Модель успешно импортирована")
            
            self._save_to_history("Импорт из ZIP")
            
        except Exception as e:
            import traceback
            tb = traceback.format_exc()
            
            messagebox.showerror("Ошибка импорта", f"Ошибка при импорте модели: {e}")
            logger.error(f"Error importing model from ZIP: {e}\n{tb}")
    
    def delete_model(self):
        """Удаляет текущую модель"""
        if not self.model_dir:
            messagebox.showwarning("Нет модели", "Нет загруженной модели для удаления")
            return
        
        slot_info = ""
        if self.original_slot:
            slot_info = f" из слота {self.original_slot}"
        
        confirm = messagebox.askyesno(
            "Удаление модели",
            f"Вы уверены, что хотите удалить модель{slot_info}?\n\nЭто действие нельзя отменить!"
        )
        
        if not confirm:
            return
        
        try:
            # Удаляем из слота
            if self.original_slot:
                slot_dir = os.path.join(MODELS_DIR, f"slot{self.original_slot}")
                if os.path.exists(slot_dir):
                    shutil.rmtree(slot_dir)
            
            # Сбрасываем состояние
            self.model_name = "Без названия"
            self.width = 700
            self.height = 700
            
            self.layers.clear()
            self.groups.clear()
            self.items.clear()
            self.imported_files.clear()
            
            self.model_dir = None
            self.original_slot = None
            
            self.model_name_var.set("Без названия")
            self.canvas_width_var.set(700)
            self.canvas_height_var.set(700)
            
            self.tree_state["expanded_groups"].clear()
            self.tree_state["selected_items"].clear()
            self.tree_state["preserve_selection"] = False
            
            self.refresh_tree()
            self.refresh_import_list()
            self.zoom_reset()
            self.redraw_canvas()
            
            # Обновляем главное окно
            if hasattr(self.master, 'app') and hasattr(self.master.app, 'refresh_slot_buttons'):
                self.master.app.refresh_slot_buttons()
            
            messagebox.showinfo("Модель удалена", f"Модель удалена{slot_info}")
            
            self._save_to_history("Удаление модели")
            
        except Exception as e:
            logger.error(f"Error deleting model: {e}")
            messagebox.showerror("Ошибка", f"Не удалось удалить модель: {e}")
    
    def cleanup_old_temp_folders(self):
        """Очищает старые временные папки"""
        import re
        
        all_folders = [f for f in os.listdir(MODELS_DIR) 
                      if os.path.isdir(os.path.join(MODELS_DIR, f))]
        
        temp_slot_pattern = re.compile(r'^temp_(\d+)_slot(\d+)$')
        model_temp_pattern = re.compile(r'^model_temp_(\d+)$')
        import_temp_pattern = re.compile(r'^import_temp_(\d+)$')
        
        temp_slot_groups = {}
        model_temp_folders = []
        import_temp_folders = []
        
        for folder in all_folders:
            match_slot = temp_slot_pattern.match(folder)
            if match_slot:
                timestamp, slot = match_slot.groups()
                key = f"slot{slot}"
                if key not in temp_slot_groups:
                    temp_slot_groups[key] = []
                temp_slot_groups[key].append((int(timestamp), folder))
                continue
            
            match_model = model_temp_pattern.match(folder)
            if match_model:
                timestamp = match_model.group(1)
                model_temp_folders.append((int(timestamp), folder))
                continue
            
            match_import = import_temp_pattern.match(folder)
            if match_import:
                timestamp = match_import.group(1)
                import_temp_folders.append((int(timestamp), folder))
                continue
        
        def remove_old_folders(folder_list, keep=3):
            folder_list.sort(key=lambda x: x[0], reverse=True)
            to_keep = folder_list[:keep]
            to_remove = folder_list[keep:]
            
            for timestamp, folder in to_remove:
                folder_path = os.path.join(MODELS_DIR, folder)
                try:
                    shutil.rmtree(folder_path)
                except Exception as e:
                    logger.error(f"Error removing old temp folder {folder}: {e}")
        
        for slot_key, folders in temp_slot_groups.items():
            remove_old_folders(folders, keep=3)
        
        remove_old_folders(model_temp_folders, keep=3)
        remove_old_folders(import_temp_folders, keep=3)
    
    def undo(self, event=None):
        """Отмена действия"""
        if self.history_index > 0:
            self.history_index -= 1
            self._load_from_history()
    
    def redo(self, event=None):
        """Повтор действия"""
        if self.history_index < len(self.history) - 1:
            self.history_index += 1
            self._load_from_history()
    
    def on_close(self):
        """Обработка закрытия окна"""
        try:
            self.audio_processor.stop()
            self.stop_blink_preview()
        except Exception as e:
            logger.error(f"Error stopping audio: {e}")
        
        # Удаляем временную папку
        if self.model_dir and "temp_" in self.model_dir and os.path.exists(self.model_dir):
            try:
                shutil.rmtree(self.model_dir)
            except Exception as e:
                logger.error(f"Error removing temporary directory: {e}")
        
        self.grab_release()
        self.destroy()
    
    # Остальные методы для совместимости
    def update_group_logic_menus(self, group_name):
        self._update_group_logic_menus(group_name)
    
    def save_to_history(self, description=""):
        self._save_to_history(description)
    
    def load_from_history(self):
        self._load_from_history()