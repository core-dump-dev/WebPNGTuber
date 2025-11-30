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
from audio import AudioProcessor
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
        # Атрибуты для GIF
        self.gif_frames = []
        self.current_frame = 0
        self.last_frame_time = 0
        self.frame_durations = []
        # Загрузка изображения
        self.original_image = None
        self.transformed_image = None
        self.tkimage = None
        self.load_original_image()
        self.update_transformed_image()
    
    def load_original_image(self):
        """Загружает оригинальное изображение без трансформаций"""
        try:
            if self.is_gif:
                with Image.open(self.image_path) as gif:
                    gif.seek(0)
                    self.original_image = gif.copy().convert("RGBA")
            else:
                self.original_image = Image.open(self.image_path).convert("RGBA")
        except Exception as e:
            logger.error(f"Ошибка загрузки изображения: {e}")
            self.original_image = Image.new("RGBA", (100, 100), (255, 0, 0, 128))
    
    def apply_transformations(self, img):
        """Применяет масштаб, поворот и отражение к изображению"""
        if not img:
            return img
        transformed = img.copy()
        # Применение отражения
        if self.flip_horizontal:
            transformed = transformed.transpose(Image.FLIP_LEFT_RIGHT)
        if self.flip_vertical:
            transformed = transformed.transpose(Image.FLIP_TOP_BOTTOM)
        # Применение масштаба
        if self.scale != 1.0:
            new_width = max(1, int(transformed.width * self.scale))
            new_height = max(1, int(transformed.height * self.scale))
            transformed = transformed.resize((new_width, new_height), Image.LANCZOS)
        # Применение поворота
        if self.rotation != 0:
            transformed = transformed.rotate(self.rotation, expand=True, resample=Image.BICUBIC)
        return transformed
    
    def update_transformed_image(self):
        """Обновляет трансформированное изображение"""
        if self.original_image:
            self.transformed_image = self.apply_transformations(self.original_image)
        # Для GIF также обновляем все кадры
        if self.is_gif and self.original_image:
            try:
                with Image.open(self.image_path) as gif:
                    self.gif_frames = []
                    self.frame_durations = []
                    for frame in range(gif.n_frames):
                        gif.seek(frame)
                        frame_img = gif.copy().convert("RGBA")
                        frame_img = self.apply_transformations(frame_img)
                        self.gif_frames.append(frame_img)
                        try:
                            duration = gif.info.get('duration', 100) / 1000.0
                            self.frame_durations.append(duration)
                        except:
                            self.frame_durations.append(0.1)
            except Exception as e:
                logger.error(f"Ошибка обновления GIF: {e}")
                self.is_gif = False
    
    def get_current_image(self):
        """Возвращает текущий кадр (для GIF) или изображение"""
        if self.is_gif and self.gif_frames:
            now = time.time()
            if now - self.last_frame_time > self.frame_durations[self.current_frame]:
                self.current_frame = (self.current_frame + 1) % len(self.gif_frames)
                self.last_frame_time = now
            return self.gif_frames[self.current_frame]
        return self.transformed_image if self.transformed_image else self.original_image

class ModelEditor(tk.Toplevel):
    def __init__(self, master, on_save=None, device='По умолчанию', noise_gate_threshold=0.01, sensitivity=1.0, thresholds=None):
        super().__init__(master)
        self.title("Редактор моделей")
        self.geometry("1400x800")
        self.on_save = on_save
        self.protocol("WM_DELETE_WINDOW", self.on_close)
        logger.info("Model editor opened")
        
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
        self.preview_fps = 24
        self.last_autosave = time.time()
        self.autosave_interval = 5.0
        self.audio_level = 0.0
        self.blink_preview_running = False
        
        # Настройки холста
        self.canvas_width = 700
        self.canvas_height = 700
        
        # Настройки зума и просмотра
        self.zoom_level = 1.0
        self.zoom_step = 0.1
        self.min_zoom = 0.1
        self.max_zoom = 5.0
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
        
        # ---- UI layout ----
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
        
        ttk.Button(left, text="Новая модель", command=self.new_model).pack(fill="x", pady=2)
        ttk.Button(left, text="Загрузить модель", command=self.load_model).pack(fill="x", pady=2)
        ttk.Button(left, text="Сохранить модель", command=self.save_model).pack(fill="x", pady=2)
        ttk.Button(left, text="Импорт PNG/GIF", command=self.import_images).pack(fill="x", pady=2)
        ttk.Button(left, text="Экспорт ZIP", command=self.export_zip).pack(fill="x", pady=2)
        
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
        ttk.Label(zoom_frame, text="Прокрутка: Колесо").pack(side="left", padx=5)
        
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
        preview_frame = ttk.LabelFrame(center, text="Предпросмотр")
        preview_frame.pack(fill="both", expand=True)
        
        # Создаем основной canvas для отображения
        self.canvas = tk.Canvas(preview_frame, bg="#222", cursor="crosshair")
        self.canvas.pack(fill="both", expand=True, padx=5, pady=5)
        
        # Привязка событий
        self.canvas.bind("<ButtonPress-1>", self.on_canvas_mouse_down)
        self.canvas.bind("<B1-Motion>", self.on_canvas_mouse_move)
        self.canvas.bind("<ButtonRelease-1>", self.on_canvas_mouse_up)
        self.canvas.bind("<MouseWheel>", self.on_canvas_zoom)  # Windows
        self.canvas.bind("<Button-4>", self.on_canvas_zoom)    # Linux
        self.canvas.bind("<Button-5>", self.on_canvas_zoom)    # Linux
        self.canvas.bind("<ButtonPress-2>", self.on_canvas_pan_start)  # Средняя кнопка для панорамирования
        self.canvas.bind("<B2-Motion>", self.on_canvas_pan_move)
        self.canvas.bind("<ButtonRelease-2>", self.on_canvas_pan_end)
        
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
        self.name_entry.bind("<FocusIn>", self.on_entry_focus)
        
        ttk.Label(grid_frame, text="X:").grid(row=1, column=0, sticky="w", padx=2, pady=2)
        self.x_entry = ttk.Entry(grid_frame)
        self.x_entry.grid(row=1, column=1, sticky="ew", padx=2, pady=2)
        self.x_entry.bind("<Return>", self.apply_props_from_entry)
        self.x_entry.bind("<FocusIn>", self.on_entry_focus)
        
        ttk.Label(grid_frame, text="Y:").grid(row=2, column=0, sticky="w", padx=2, pady=2)
        self.y_entry = ttk.Entry(grid_frame)
        self.y_entry.grid(row=2, column=1, sticky="ew", padx=2, pady=2)
        self.y_entry.bind("<Return>", self.apply_props_from_entry)
        self.y_entry.bind("<FocusIn>", self.on_entry_focus)
        
        ttk.Label(grid_frame, text="Масштаб:").grid(row=3, column=0, sticky="w", padx=2, pady=2)
        self.scale_entry = ttk.Entry(grid_frame)
        self.scale_entry.grid(row=3, column=1, sticky="ew", padx=2, pady=2)
        self.scale_entry.bind("<Return>", self.apply_props_from_entry)
        self.scale_entry.bind("<FocusIn>", self.on_entry_focus)
        
        ttk.Label(grid_frame, text="Поворот:").grid(row=4, column=0, sticky="w", padx=2, pady=2)
        self.rotation_entry = ttk.Entry(grid_frame)
        self.rotation_entry.grid(row=4, column=1, sticky="ew", padx=2, pady=2)
        self.rotation_entry.bind("<Return>", self.apply_props_from_entry)
        self.rotation_entry.bind("<FocusIn>", self.on_entry_focus)
        
        # Зеркалирование
        mirror_frame = ttk.Frame(props)
        mirror_frame.pack(fill="x", padx=5, pady=2)
        ttk.Label(mirror_frame, text="Зеркалирование:").pack(side="left")
        self.flip_h_var = tk.BooleanVar(value=False)
        self.flip_v_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(mirror_frame, text="Гор.", variable=self.flip_h_var, 
                       command=self.on_mirror_change).pack(side="left", padx=5)
        ttk.Checkbutton(mirror_frame, text="Верт.", variable=self.flip_v_var,
                       command=self.on_mirror_change).pack(side="left", padx=5)
        
        self.visible_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(props, text="Видимый", variable=self.visible_var).pack(anchor="w", padx=5, pady=(0, 5))
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
            # Создаем OptionMenu с пустым значением по умолчанию
            initial_value = ""
            om = ttk.OptionMenu(row, self.state_vars[s], initial_value)
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
        
        # Кнопка применения
        ttk.Button(groups_frame, text="Применить логику", command=self.apply_group_logic).pack(fill="x", pady=10)
        
        try:
            self.iconbitmap(os.path.join(BASE_DIR, 'favicon.ico'))
        except Exception as e:
            logger.error(f"Error loading icon: {e}")
        
        # Запуск превью
        self.after(100, self._preview_loop)
    
    def update_model_name(self, event=None):
        """Обновление имени модели"""
        self.model["name"] = self.model_name_var.get()
        logger.info(f"Model name updated to: {self.model['name']}")
    
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
        if group.get("random_effect", False) and self.random_effect_var.get():
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
    
    def update_group_logic_menus(self, group_name):
        """Обновляет выпадающие меню для логики группы"""
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
        """Загрузка настроек выбранной группы в интерфейс"""
        group = next((g for g in self.model.get("groups", []) if g.get("name") == group_name), None)
        if not group:
            return
            
        # Сначала обновляем меню
        self.update_group_logic_menus(group_name)
        
        logic = group.get("logic", {})
        for state in ["silent", "whisper", "normal", "shout", "blink", "open"]:
            if state in logic:
                self.state_vars[state].set(logic[state])
            else:
                self.state_vars[state].set("")
                
        # Загрузка настроек моргания
        blink_freq = group.get("blink_freq", 0.0)
        self.blink_freq.set(blink_freq)
        
        # Загрузка настроек случайного эффекта
        self.random_effect_var.set(group.get("random_effect", False))
        self.random_min_var.set(group.get("random_min", 5.0))
        self.random_max_var.set(group.get("random_max", 10.0))
    
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
    
    def redraw_canvas(self, level=0.0, mode="none"):
        try:
            # Очищаем canvas
            self.canvas.delete("all")
            
            # Получаем размеры canvas
            canvas_width = self.canvas.winfo_width()
            canvas_height = self.canvas.winfo_height()
            if canvas_width <= 1 or canvas_height <= 1:
                return
                
            # Рассчитываем центр с учетом смещения и зума
            scaled_width = self.canvas_width * self.zoom_level
            scaled_height = self.canvas_height * self.zoom_level
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
            
            # Создаем временное изображение для композиции
            temp_image = Image.new("RGBA", (int(scaled_width), int(scaled_height)), (0, 0, 0, 0))
            
            # Определяем список элементов для отображения
            items_to_draw = []
            
            if mode == "none":
                # Режим редактирования - все видимые слои
                items_to_draw = [ci for ci in self.items if ci.visible]
            else:
                # Режим тестирования - используем логику групп
                # Определяем текущее состояние на основе уровня
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
            
            # Сортируем элементы для правильного наложения
            items_to_draw.sort(key=lambda x: self.items.index(x))
            
            # Отрисовываем все элементы
            for ci in items_to_draw:
                if not ci.visible:
                    continue
                    
                img = ci.get_current_image()
                if not img:
                    continue
                    
                # Масштабируем изображение для текущего зума
                if self.zoom_level != 1.0:
                    scaled_img_width = int(img.width * self.zoom_level)
                    scaled_img_height = int(img.height * self.zoom_level)
                    if scaled_img_width > 0 and scaled_img_height > 0:
                        scaled_img = img.resize((scaled_img_width, scaled_img_height), Image.LANCZOS)
                    else:
                        scaled_img = img
                else:
                    scaled_img = img
                    
                # Рассчитываем позицию на временном изображении
                px = int((scaled_width // 2) - scaled_img.width // 2 + (ci.x * self.zoom_level))
                py = int((scaled_height // 2) - scaled_img.height // 2 + (ci.y * self.zoom_level))
                
                try:
                    temp_image.alpha_composite(scaled_img, (px, py))
                except Exception as e:
                    logger.error(f"Ошибка композиции для {ci.layer.get('name')}: {e}")
            
            # Конвертируем изображение в PhotoImage и отображаем
            try:
                self.canvas_image = ImageTk.PhotoImage(temp_image)
                self.canvas.create_image(canvas_x1, canvas_y1, anchor="nw", image=self.canvas_image)
            except Exception as e:
                logger.error(f"Ошибка отображения: {e}")
                
            # Выделение выбранных элементов (только в режиме редактирования)
            if mode == "none":
                for ci in self.items:
                    if ci.layer.get("_selected"):
                        img = ci.get_current_image()
                        if not img:
                            continue
                            
                        # Масштабируем для выделения
                        if self.zoom_level != 1.0:
                            scaled_img_width = int(img.width * self.zoom_level)
                            scaled_img_height = int(img.height * self.zoom_level)
                        else:
                            scaled_img_width = img.width
                            scaled_img_height = img.height
                            
                        px = canvas_x1 + int((scaled_width // 2) - scaled_img_width // 2 + (ci.x * self.zoom_level))
                        py = canvas_y1 + int((scaled_height // 2) - scaled_img_height // 2 + (ci.y * self.zoom_level))
                        
                        self.canvas.create_rectangle(
                            px, py, px + scaled_img_width, py + scaled_img_height,
                            outline="cyan", width=2
                        )
        except Exception as e:
            logger.error(f"Error in redraw_canvas: {e}")
            import traceback
            logger.error(traceback.format_exc())
    
    def group_selected(self):
        """Создание новой группы из выбранных элементов"""
        if not self.current_selection:
            messagebox.showwarning("Группа", "Выберите хотя бы один элемент")
            return
            
        # Запрашиваем имя новой группы
        name = simpledialog.askstring("Имя группы", "Введите имя новой группы", parent=self)
        if not name:
            return
            
        # Проверяем, что имя не занято
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
            
        # Создаем новую группу
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
        
        # Добавляем группу в модель
        self.model.setdefault("groups", []).append(new_group)
        
        # Устанавливаем новую группу для выбранных элементов
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
        
        # Снимаем выделение с элементов
        for ci in self.items:
            ci.layer["_selected"] = False
        self.current_selection = []
        self.selected_group = name
        self.refresh_tree()
        self.redraw_canvas()
        logger.info(f"Created new group: {name} with parent {parent_group}")
    
    def refresh_tree(self):
        """Обновление древовидного списка с поддержкой вложенных групп"""
        try:
            self.tree.delete(*self.tree.get_children())
            
            # Строим иерархию групп
            groups = self.model.get("groups", [])
            root_groups = [g for g in groups if not g.get("parent")]
            
            # Собираем элементы по группам
            items_by_group = {}
            ungrouped_items = []
            for ci in self.items:
                group_name = ci.layer.get("group")
                if group_name:
                    if group_name not in items_by_group:
                        items_by_group[group_name] = []
                    items_by_group[group_name].append(ci)
                else:
                    ungrouped_items.append(ci)
            
            # Рекурсивная функция для добавления групп и элементов в дерево
            def add_group_to_tree(parent_id, group):
                group_id = self.tree.insert(
                    parent_id, "end", 
                    text=f"📁 {group['name']}", 
                    values=("group", group['name'])
                )
                
                # Добавляем элементы группы
                group_items = items_by_group.get(group['name'], [])
                for ci in group_items:
                    self.tree.insert(
                        group_id, "end", 
                        text=self._get_item_display_text(ci), 
                        values=("item", id(ci))
                    )
                
                # Добавляем дочерние группы
                child_groups = [g for g in groups if g.get("parent") == group['name']]
                for child_group in child_groups:
                    add_group_to_tree(group_id, child_group)
                
                return group_id
            
            # Добавляем элементы без групп
            for ci in ungrouped_items:
                self.tree.insert("", "end", text=self._get_item_display_text(ci), values=("item", id(ci)))
            
            # Добавляем корневые группы
            for group in root_groups:
                add_group_to_tree("", group)
            
            # Если выбрана группа, обновляем меню
            if self.selected_group:
                self.update_group_logic_menus(self.selected_group)
        except Exception as e:
            logger.error(f"Error refreshing tree: {e}")
            import traceback
            logger.error(traceback.format_exc())
    
    def _get_item_display_text(self, ci):
        """Получает текст для отображения элемента в дереве"""
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
        """Находит элемент по его id"""
        for ci in self.items:
            if id(ci) == item_id:
                return ci
        return None
    
    def on_tree_select(self, event=None):
        """Обработка выбора в дереве элементов"""
        try:
            selection = self.tree.selection()
            self.current_selection = []
            self.selected_group = None
            
            # Снимаем выделение со всех элементов
            for c in self.items:
                c.layer["_selected"] = False
            
            if not selection:
                self.group_label.config(text="(нет группы)")
                self.clear_props_fields()
                self.redraw_canvas()
                return
                
            # Обрабатываем все выбранные элементы
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
                    # Выбираем все элементы внутри группы
                    for ci in self.items:
                        if ci.layer.get("group") == group_name:
                            ci.layer["_selected"] = True
                            selected_items.add(ci)
                    # Также выбираем элементы из вложенных групп
                    def select_items_in_subgroups(group_name):
                        for g in self.model.get("groups", []):
                            if g.get("parent") == group_name:
                                for ci in self.items:
                                    if ci.layer.get("group") == g.get("name"):
                                        ci.layer["_selected"] = True
                                        selected_items.add(ci)
                                select_items_in_subgroups(g.get("name"))
                    
                    select_items_in_subgroups(group_name)
                elif item_type == "item":
                    # Выбран отдельный элемент
                    item_id = int(item_data)
                    ci = self._get_item_by_id(item_id)
                    if ci:
                        ci.layer["_selected"] = True
                        selected_items.add(ci)
            
            # Формируем итоговый список выбранных элементов
            self.current_selection = list(selected_items)
            
            # Определяем выбранную группу
            if len(selected_groups) == 1:
                self.selected_group = list(selected_groups)[0]
                self.group_label.config(text=self.selected_group)
                self.load_group_settings(self.selected_group)
            else:
                self.selected_group = None
                self.group_label.config(text="(нет группы)")
                # Загружаем свойства первого выбранного элемента, если есть
                if self.current_selection:
                    self.load_item_props(self.current_selection[0])
                else:
                    self.clear_props_fields()
            
            self.redraw_canvas()
        except Exception as e:
            logger.error(f"Error in on_tree_select: {e}")
            import traceback
            logger.error(traceback.format_exc())
    
    def bring_forward(self):
        """Переместить выбранные элементы вперед"""
        if not self.current_selection:
            return
            
        # Обрабатываем каждую группу отдельно
        groups_to_update = {}
        for ci in self.current_selection:
            group_name = ci.layer.get("group")
            if group_name not in groups_to_update:
                groups_to_update[group_name] = []
            groups_to_update[group_name].append(ci)
        
        # Для каждой группы перемещаем элементы
        for group_name, items in groups_to_update.items():
            if group_name:
                # Находим все элементы в этой группе
                group_items = [i for i in self.items if i.layer.get("group") == group_name]
                group_indices = {i: idx for idx, i in enumerate(group_items)}
                
                # Сортируем выбранные элементы по их текущему положению
                items.sort(key=lambda x: group_indices[x])
                
                # Перемещаем элементы вперед
                for ci in items:
                    idx = group_items.index(ci)
                    if idx < len(group_items) - 1:
                        # Меняем местами с следующим элементом
                        group_items[idx], group_items[idx+1] = group_items[idx+1], group_items[idx]
                
                # Обновляем порядок элементов в общей коллекции
                for i, item in enumerate(group_items):
                    global_idx = self.items.index(item)
                    # Находим следующий элемент из той же группы в общей коллекции
                    next_idx = global_idx + 1
                    while next_idx < len(self.items) and self.items[next_idx].layer.get("group") != group_name:
                        next_idx += 1
                    if next_idx < len(self.items):
                        self.items[global_idx], self.items[next_idx] = self.items[next_idx], self.items[global_idx]
            else:
                # Элементы без группы
                for ci in items:
                    idx = self.items.index(ci)
                    if idx < len(self.items) - 1:
                        self.items[idx], self.items[idx+1] = self.items[idx+1], self.items[idx]
        
        self.refresh_tree()
        self.redraw_canvas()
    
    def send_backward(self):
        """Переместить выбранные элементы назад"""
        if not self.current_selection:
            return
            
        # Обрабатываем каждую группу отдельно
        groups_to_update = {}
        for ci in self.current_selection:
            group_name = ci.layer.get("group")
            if group_name not in groups_to_update:
                groups_to_update[group_name] = []
            groups_to_update[group_name].append(ci)
        
        # Для каждой группы перемещаем элементы
        for group_name, items in groups_to_update.items():
            if group_name:
                # Находим все элементы в этой группе
                group_items = [i for i in self.items if i.layer.get("group") == group_name]
                group_indices = {i: idx for idx, i in enumerate(group_items)}
                
                # Сортируем выбранные элементы по их текущему положению в обратном порядке
                items.sort(key=lambda x: group_indices[x], reverse=True)
                
                # Перемещаем элементы назад
                for ci in items:
                    idx = group_items.index(ci)
                    if idx > 0:
                        # Меняем местами с предыдущим элементом
                        group_items[idx], group_items[idx-1] = group_items[idx-1], group_items[idx]
                
                # Обновляем порядок элементов в общей коллекции
                for i, item in enumerate(group_items):
                    global_idx = self.items.index(item)
                    # Находим предыдущий элемент из той же группы в общей коллекции
                    prev_idx = global_idx - 1
                    while prev_idx >= 0 and self.items[prev_idx].layer.get("group") != group_name:
                        prev_idx -= 1
                    if prev_idx >= 0:
                        self.items[global_idx], self.items[prev_idx] = self.items[prev_idx], self.items[global_idx]
            else:
                # Элементы без группы
                for ci in items:
                    idx = self.items.index(ci)
                    if idx > 0:
                        self.items[idx], self.items[idx-1] = self.items[idx-1], self.items[idx]
        
        self.refresh_tree()
        self.redraw_canvas()
    
    def apply_group_logic(self):
        if not self.selected_group:
            messagebox.showwarning("Нет группы", "Сначала выберите группу")
            return
            
        gname = self.selected_group
        grp = None
        for g in self.model.get("groups", []):
            if g.get("name") == gname:
                grp = g
                break
                
        if not grp:
            messagebox.showerror("Ошибка", "Группа не найдена")
            return
            
        # Сохраняем логику для каждого состояния
        logic = {}
        for s, var in self.state_vars.items():
            val = var.get().strip()
            if val:
                logic[s] = val
                
        grp["logic"] = logic
        
        # Сохраняем настройки моргания
        try:
            blink_freq_value = self.blink_freq.get()
            if blink_freq_value == "" or blink_freq_value is None:
                blink_freq_value = 0.0
            grp["blink_freq"] = float(blink_freq_value)
        except Exception as e:
            logger.error(f"Error converting blink_freq: {e}")
            grp["blink_freq"] = 0.0
        
        # Сохраняем настройки случайного эффекта
        grp["random_effect"] = self.random_effect_var.get()
        grp["random_min"] = self.random_min_var.get()
        grp["random_max"] = self.random_max_var.get()
        
        messagebox.showinfo("Логика группы", f"Логика для группы {gname} сохранена")
        logger.info(f"Group logic applied to {gname}: {logic}")
    
    def on_mirror_change(self):
        """Обработка изменения зеркалирования"""
        if not self.current_selection:
            return
            
        for ci in self.current_selection:
            ci.flip_horizontal = self.flip_h_var.get()
            ci.flip_vertical = self.flip_v_var.get()
            ci.update_transformed_image()
            
        self.redraw_canvas()
    
    def on_entry_focus(self, event):
        """Обработка фокуса на поле ввода - не сбрасываем выделение"""
        return "break"
    
    def zoom_in(self):
        """Увеличение масштаба"""
        self.zoom_level = min(self.max_zoom, self.zoom_level + self.zoom_step)
        self.redraw_canvas()
    
    def zoom_out(self):
        """Уменьшение масштаба"""
        self.zoom_level = max(self.min_zoom, self.zoom_level - self.zoom_step)
        self.redraw_canvas()
    
    def zoom_reset(self):
        """Сброс масштаба и позиции"""
        self.zoom_level = 1.0
        self.offset_x = 0
        self.offset_y = 0
        self.redraw_canvas()
    
    def on_canvas_zoom(self, event):
        """Обработка зума колесиком мыши"""
        if event.delta > 0 or event.num == 4:
            self.zoom_in()
        else:
            self.zoom_out()
    
    def on_canvas_pan_start(self, event):
        """Начало перемещения холста"""
        self.is_panning = True
        self.last_pan_x = event.x
        self.last_pan_y = event.y
        self.canvas.config(cursor="fleur")
    
    def on_canvas_pan_move(self, event):
        """Перемещение холста"""
        if self.is_panning:
            dx = event.x - self.last_pan_x
            dy = event.y - self.last_pan_y
            self.offset_x += dx
            self.offset_y += dy
            self.last_pan_x = event.x
            self.last_pan_y = event.y
            self.redraw_canvas()
    
    def on_canvas_pan_end(self, event):
        """Конец перемещения холста"""
        self.is_panning = False
        self.canvas.config(cursor="crosshair")
    
    def update_canvas_size(self, event=None):
        """Обновление размера холста"""
        try:
            new_width = max(100, min(3000, self.canvas_width_var.get()))
            new_height = max(100, min(3000, self.canvas_height_var.get()))
            self.canvas_width = new_width
            self.canvas_height = new_height
            self.canvas_width_var.set(new_width)
            self.canvas_height_var.set(new_height)
            self.model["width"] = new_width
            self.model["height"] = new_height
            self.zoom_reset()
            logger.info(f"Canvas size updated to: {new_width}x{new_height}")
        except Exception as e:
            messagebox.showerror("Ошибка", f"Неверный размер холста: {e}")
            logger.error(f"Error updating canvas size: {e}")
    
    def on_close(self):
        """Обработка закрытия окна"""
        try:
            self.audio_processor.stop()
            self.stop_blink_preview()
        except Exception as e:
            logger.error(f"Error stopping audio: {e}")
        self.cleanup_temp_folders()
        self.grab_release()
        logger.info("Model editor closed")
        self.destroy()
    
    def cleanup_temp_folders(self):
        """Удаление всех временных папок в models"""
        temp_folders = glob.glob(os.path.join(MODELS_DIR, "temp_*"))
        for folder in temp_folders:
            try:
                if os.path.isdir(folder):
                    shutil.rmtree(folder)
            except Exception as e:
                logger.error(f"Error cleaning up temp folder {folder}: {e}")
    
    def update_test_mode(self):
        """Обновление режима тестирования"""
        mode = self.test_mode_var.get()
        
        # Показываем/скрываем индикатор уровня
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
        """Обработка уровня аудио"""
        level_scaled = level * self.mic_sensitivity
        if self.test_mode_var.get() == "microphone":
            self.level_bar["value"] = level_scaled * 100
        self.audio_level = level_scaled
    
    def new_model(self):
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
        self.refresh_tree()
        self.zoom_reset()
        self.redraw_canvas()
        logger.info(f"New model created: {name}")
    
    def load_model(self):
        slot_dialog = tk.Toplevel(self)
        slot_dialog.title("Загрузка из слота")
        slot_dialog.geometry("300x200")
        slot_dialog.transient(self)
        slot_dialog.grab_set()
        
        ttk.Label(slot_dialog, text="Выберите слот для загрузки:").pack(pady=10)
        
        for i in range(1, 7):
            slot_dir = os.path.join(MODELS_DIR, f"slot{i}")
            json_path = os.path.join(slot_dir, "model.json")
            if os.path.exists(json_path):
                btn_text = f"Слот {i} (есть модель)"
            else:
                btn_text = f"Слот {i} (пустой)"
                
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
            
        with open(json_path, "r", encoding="utf-8") as f:
            self.model = json.load(f)
            
        # Мигрируем старую модель для поддержки вложенных групп
        self._migrate_model_for_nested_groups()
        
        # Загружаем имя модели
        self.model_name_var.set(self.model.get("name", "Без названия"))
        
        # Загружаем размеры холста
        self.canvas_width = self.model.get("width", 700)
        self.canvas_height = self.model.get("height", 700)
        self.canvas_width_var.set(self.canvas_width)
        self.canvas_height_var.set(self.canvas_height)
        
        temp_dir = os.path.join(MODELS_DIR, f"temp_{int(time.time())}_slot{slot_num}")
        os.makedirs(temp_dir, exist_ok=True)
        
        for f in os.listdir(path):
            src = os.path.join(path, f)
            if os.path.isfile(src):
                shutil.copy2(src, os.path.join(temp_dir, f))
                
        self.model_dir = temp_dir
        self.original_slot = slot_num
        
        self.items.clear()
        for layer in self.model.get("layers", []):
            filename = layer.get("file")
            if not filename:
                continue
            fp = os.path.join(self.model_dir, filename)
            if not os.path.exists(fp):
                continue
                
            try:
                ci = CanvasItem(layer, fp)
                self.items.append(ci)
            except Exception as e:
                logger.error(f"Error loading image: {e}")
                
        self.imported_files.clear()
        for f in os.listdir(self.model_dir):
            if f.lower().endswith((".png", ".gif")):
                try:
                    fp = os.path.join(self.model_dir, f)
                    with Image.open(fp) as img:
                        is_gif = img.format == "GIF" and img.is_animated
                        img.seek(0)
                        preview_img = img.copy().convert("RGBA")
                    self.imported_files.append((f, preview_img, is_gif))
                except Exception as e:
                    logger.error(f"Error loading imported file: {e}")
                    
        self.refresh_import_list()
        self.refresh_tree()
        self.zoom_reset()
        self.redraw_canvas()
        logger.info(f"Model loaded from slot {slot_num}")
    
    def _migrate_model_for_nested_groups(self):
        """Миграция старой модели для поддержки вложенных групп"""
        groups = self.model.get("groups", [])
        # Если в модели нет поля 'parent' для групп, добавляем его
        for group in groups:
            if "parent" not in group:
                group["parent"] = None
    
    def save_model(self):
        if not self.model_dir:
            name = self.model.get("name", "model")
            folder = filedialog.askdirectory(title="Выберите папку для модели")
            if not folder:
                return
                
            self.model_dir = os.path.join(folder, name.replace(" ", "_"))
            os.makedirs(self.model_dir, exist_ok=True)
            
        # Сохраняем имя модели и размеры холста
        self.model["name"] = self.model_name_var.get()
        self.model["width"] = self.canvas_width
        self.model["height"] = self.canvas_height
            
        self.model["layers"] = []
        for ci in self.items:
            layer = ci.layer
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
            self.model["layers"].append(layer)
            
        model_json_path = os.path.join(self.model_dir, "model.json")
        with open(model_json_path, "w", encoding="utf-8") as f:
            json.dump(self.model, f, indent=2, ensure_ascii=False)
            
        self.create_preview()
        self.show_save_slot_dialog()
        
        if self.on_save:
            self.on_save(self.model, self.model_dir)
            
        self.last_autosave = time.time()
        logger.info("Model saved")
    
    def show_save_slot_dialog(self):
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
            if os.path.exists(json_path):
                btn_text = f"Слот {i} (перезаписать)"
            else:
                btn_text = f"Слот {i} (новый)"
                
            btn = ttk.Button(
                slots_frame, 
                text=btn_text,
                width=20,
                command=lambda i=i: self._save_slot(i, slot_dialog)
            )
            btn.pack(fill="x", padx=10, pady=3)
            
        ttk.Button(
            slot_dialog, 
            text="Отмена", 
            command=slot_dialog.destroy
        ).pack(fill="x", padx=20, pady=10)
    
    def _save_slot(self, slot_num, dialog):
        dialog.destroy()
        slot_dir = os.path.join(MODELS_DIR, f"slot{slot_num}")
        os.makedirs(slot_dir, exist_ok=True)
        
        for f in os.listdir(slot_dir):
            file_path = os.path.join(slot_dir, f)
            try:
                if os.path.isfile(file_path):
                    os.unlink(file_path)
            except Exception as e:
                logger.error(f"Error deleting file {file_path}: {e}")
                
        for f in os.listdir(self.model_dir):
            src = os.path.join(self.model_dir, f)
            if os.path.isfile(src):
                shutil.copy2(src, os.path.join(slot_dir, f))
                
        messagebox.showinfo("Сохранено", f"Модель сохранена в слот {slot_num}")
        
        if hasattr(self.master, 'app') and hasattr(self.master.app, 'refresh_slot_buttons'):
            self.master.app.refresh_slot_buttons()
            
        if "temp_" in self.model_dir:
            try:
                shutil.rmtree(self.model_dir)
            except Exception as e:
                logger.error(f"Error removing temp directory: {e}")
            self.model_dir = None
            
        logger.info(f"Model saved to slot {slot_num}")
    
    def create_preview(self):
        if not self.model_dir:
            return
            
        base = Image.new("RGBA", (self.canvas_width, self.canvas_height), (0, 0, 0, 0))
        center_x = self.canvas_width // 2
        center_y = self.canvas_height // 2
        
        # Используем все видимые элементы для превью (не только по логике групп)
        visible_items = [ci for ci in self.items if ci.visible]
        
        for ci in visible_items:
            img = ci.get_current_image()
            if not img:
                continue
                
            px = center_x - img.size[0] // 2 + int(ci.x)
            py = center_y - img.size[1] // 2 + int(ci.y)
            
            try:
                base.alpha_composite(img, (px, py))
            except Exception as e:
                logger.error(f"Error creating preview: {e}")
                
        base.thumbnail((200, 200))
        preview_path = os.path.join(self.model_dir, "preview.png")
        base.save(preview_path)
    
    def import_images(self):
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
            
        for p in files:
            try:
                base = os.path.basename(p)
                dest = os.path.join(self.model_dir, base)
                if os.path.abspath(p) != os.path.abspath(dest):
                    shutil.copy2(p, dest)
                    
                is_gif = False
                if base.lower().endswith('.gif'):
                    with Image.open(p) as img:
                        is_gif = img.is_animated
                        
                with Image.open(p) as img:
                    img.seek(0)
                    preview_img = img.copy().convert("RGBA")
                    
                self.imported_files.append((base, preview_img, is_gif))
                
                layer = {
                    "name": os.path.splitext(base)[0], 
                    "file": base, 
                    "blink": False, 
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
                
                image_path = os.path.join(self.model_dir, base)
                ci = CanvasItem(layer, image_path)
                self.items.append(ci)
            except Exception as e:
                logger.error(f"Error importing image {p}: {e}")
                
        self.refresh_import_list()
        self.refresh_tree()
        self.redraw_canvas()
        self.last_autosave = time.time()
        logger.info(f"Imported {len(files)} images")
    
    def refresh_import_list(self):
        for w in self.import_inner.winfo_children():
            w.destroy()
            
        for i, (fname, img, is_gif) in enumerate(self.imported_files):
            row = ttk.Frame(self.import_inner)
            row.pack(fill="x", padx=2, pady=2)
            
            if is_gif:
                icon = "GIF"
            else:
                icon = "PNG"
                
            ttk.Label(row, text=f"{icon}: {fname}", width=20).pack(side="left", padx=2)
            ttk.Button(row, text="+", width=2, command=lambda f=fname: self.add_to_canvas(f)).pack(side="left", padx=2)
            ttk.Button(row, text="-", width=2, command=lambda f=fname: self.remove_from_canvas_by_file(f)).pack(side="left", padx=2)
            ttk.Button(row, text="🗑️", width=2, command=lambda f=fname: self.delete_file(f)).pack(side="left", padx=2)
    
    def clear_props_fields(self):
        """Очистка полей свойств элемента"""
        self.name_entry.delete(0, "end")
        self.x_entry.delete(0, "end")
        self.y_entry.delete(0, "end")
        self.scale_entry.delete(0, "end")
        self.rotation_entry.delete(0, "end")
        self.flip_h_var.set(False)
        self.flip_v_var.set(False)
        self.visible_var.set(True)
    
    def load_item_props(self, ci):
        """Загрузка свойств выбранного элемента"""
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
        """Применение свойств при нажатии Enter в поле ввода"""
        self.apply_props()
    
    def apply_props(self):
        if not self.current_selection:
            messagebox.showwarning("Нет выбора", "Сначала выберите элемент")
            return
            
        for ci in self.current_selection:
            name = self.name_entry.get().strip()
            try:
                x = int(self.x_entry.get().strip())
                y = int(self.y_entry.get().strip())
                scale = float(self.scale_entry.get().strip())
                rotation = int(self.rotation_entry.get().strip())
            except Exception as e:
                messagebox.showwarning("Ошибка", "X и Y должны быть целыми числами, масштаб - дробным, поворот - целым")
                logger.error(f"Error applying properties: {e}")
                return
                
            vis = self.visible_var.get()
            if name:
                ci.layer["name"] = name
            ci.x = x
            ci.y = y
            ci.visible = vis
            
            need_redraw = False
            if scale != ci.scale:
                ci.scale = scale
                ci.update_transformed_image()
                need_redraw = True
                
            if rotation != ci.rotation:
                ci.rotation = rotation
                ci.update_transformed_image()
                need_redraw = True
                
            ci.flip_horizontal = self.flip_h_var.get()
            ci.flip_vertical = self.flip_v_var.get()
            
            if ci.flip_horizontal or ci.flip_vertical:
                ci.update_transformed_image()
                need_redraw = True
                
        if need_redraw:
            self.redraw_canvas()
            
        self.refresh_tree()
        self.last_autosave = time.time()
    
    def ungroup_selected(self):
        if self.selected_group:
            gname = self.selected_group
            # Получаем группу
            group = next((g for g in self.model.get("groups", []) if g.get("name") == gname), None)
            if not group:
                return
                
            parent_group = group.get("parent")
            
            # Если есть родительская группа, переносим элементы в нее
            if parent_group:
                parent = next((g for g in self.model.get("groups", []) if g.get("name") == parent_group), None)
                if parent:
                    # Переносим элементы в родительскую группу
                    for ci in self.items:
                        if ci.layer.get("group") == gname:
                            ci.layer["group"] = parent_group
                            if ci.layer.get("name") not in parent.get("children", []):
                                parent.setdefault("children", []).append(ci.layer.get("name"))
                    # Удаляем ссылку на группу из родительской группы
                    if gname in parent.get("children", []):
                        parent["children"].remove(gname)
            
            # Удаляем группу
            self.model["groups"] = [g for g in self.model.get("groups", []) if g.get("name") != gname]
            
            # Очищаем группу у элементов
            for ci in self.items:
                if ci.layer.get("group") == gname:
                    ci.layer["group"] = parent_group if parent_group else None
                    
            self.selected_group = parent_group
            self.refresh_tree()
            self.redraw_canvas()
            logger.info(f"Ungrouped group: {gname}")
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
                            self.model["groups"].remove(g)
                ci.layer["group"] = None
                
        self.refresh_tree()
        self.redraw_canvas()
        logger.info(f"Ungrouped {len(self.current_selection)} items")
    
    def on_canvas_mouse_down(self, event):
        # Предотвращаем обработку, если фокус в поле ввода
        focus_widget = self.focus_get()
        if focus_widget and isinstance(focus_widget, (ttk.Entry, tk.Entry)):
            return
            
        # Получаем координаты на холсте с учетом зума и смещения
        canvas_width = self.canvas.winfo_width()
        canvas_height = self.canvas.winfo_height()
        scaled_width = self.canvas_width * self.zoom_level
        scaled_height = self.canvas_height * self.zoom_level
        center_x = canvas_width // 2 + self.offset_x
        center_y = canvas_height // 2 + self.offset_y
        canvas_x1 = center_x - scaled_width // 2
        canvas_y1 = center_y - scaled_height // 2
        
        # Преобразуем координаты мыши в координаты холста
        mx = event.x - canvas_x1
        my = event.y - canvas_y1
        
        # Масштабируем координаты обратно к оригинальному размеру холста
        if self.zoom_level > 0:
            mx = mx / self.zoom_level
            my = my / self.zoom_level
            
        # Сбрасываем выделение со всех элементов
        for c in self.items:
            c.layer["_selected"] = False
            
        self.current_selection = []
        self.drag_data = {"item": None, "x": mx, "y": my, "group_items": []}
        found = None
        
        # Поиск элемента под курсором (в обратном порядке для правильного Z-порядка)
        for ci in reversed(self.items):
            if not ci.visible:
                continue
                
            img = ci.get_current_image()
            if not img:
                continue
                
            # Рассчитываем позицию элемента на холсте
            px = (self.canvas_width // 2) - img.width // 2 + ci.x
            py = (self.canvas_height // 2) - img.height // 2 + ci.y
            
            # Проверяем попадание в bounding box элемента
            if px <= mx <= px + img.width and py <= my <= py + img.height:
                # Проверяем прозрачность пикселя
                try:
                    if img.mode == 'RGBA':
                        # Получаем пиксель под курсором
                        pixel_x = int(mx - px)
                        pixel_y = int(my - py)
                        if 0 <= pixel_x < img.width and 0 <= pixel_y < img.height:
                            pixel = img.getpixel((pixel_x, pixel_y))
                            if len(pixel) >= 4 and pixel[3] > 0:  # Не прозрачный пиксель
                                found = ci
                                break
                    else:
                        found = ci
                        break
                except Exception as e:
                    logger.error(f"Error checking pixel: {e}")
                    found = ci
                    break
        
        if found:
            # Если нажат Ctrl - инвертируем выделение
            if event.state & 0x0004:  # Ctrl
                found.layer["_selected"] = not bool(found.layer.get("_selected"))
                if found.layer["_selected"]:
                    self.current_selection.append(found)
                else:
                    if found in self.current_selection:
                        self.current_selection.remove(found)
            else:
                # Снимаем выделение со всех элементов
                for c in self.items:
                    c.layer["_selected"] = False
                    
                # Выделяем найденный элемент
                found.layer["_selected"] = True
                self.current_selection = [found]
                
            # Если выделена группа, сохраняем все элементы группы для перемещения
            if self.selected_group:
                self.drag_data["group_items"] = self._get_all_group_items(self.selected_group)
            else:
                self.drag_data["group_items"] = self.current_selection.copy()
                
            self.drag_data["item"] = found
            self.refresh_tree()
        else:
            # Если кликнули вне элементов - сбрасываем выделение
            for c in self.items:
                c.layer["_selected"] = False
            self.current_selection = []
            self.selected_group = None
            self.refresh_tree()
    
    def on_canvas_mouse_move(self, event):
        if not self.drag_data.get("item") or not self.drag_data.get("group_items"):
            return
            
        # Получаем координаты на холсте с учетом зума и смещения
        canvas_width = self.canvas.winfo_width()
        canvas_height = self.canvas.winfo_height()
        scaled_width = self.canvas_width * self.zoom_level
        scaled_height = self.canvas_height * self.zoom_level
        center_x = canvas_width // 2 + self.offset_x
        center_y = canvas_height // 2 + self.offset_y
        canvas_x1 = center_x - scaled_width // 2
        canvas_y1 = center_y - scaled_height // 2
        
        # Преобразуем координаты мыши в координаты холста
        mx = event.x - canvas_x1
        my = event.y - canvas_y1
        
        # Масштабируем координаты обратно к оригинальному размеру холста
        if self.zoom_level > 0:
            mx = mx / self.zoom_level
            my = my / self.zoom_level
            
        dx = mx - self.drag_data["x"]
        dy = my - self.drag_data["y"]
        self.drag_data["x"] = mx
        self.drag_data["y"] = my
        
        # Перемещаем все элементы из группы
        for ci in self.drag_data["group_items"]:
            ci.x += int(dx)
            ci.y += int(dy)
            
        # Обновляем поля координат в реальном времени
        if len(self.current_selection) == 1:
            self.x_entry.delete(0, "end")
            self.x_entry.insert(0, str(self.current_selection[0].x))
            self.y_entry.delete(0, "end")
            self.y_entry.insert(0, str(self.current_selection[0].y))
            
        self.refresh_tree()
        self.redraw_canvas()
    
    def on_canvas_mouse_up(self, event):
        self.drag_data["item"] = None
        self.last_autosave = time.time()
    
    def add_to_canvas(self, filename):
        for fname, img, is_gif in self.imported_files:
            if fname == filename:
                layer = None
                for l in self.model.get("layers", []):
                    if l.get("file") == fname:
                        layer = l
                        break
                        
                if not layer:
                    layer = {
                        "name": os.path.splitext(fname)[0], 
                        "file": fname, 
                        "blink": False, 
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
                    
                image_path = os.path.join(self.model_dir, fname)
                ci = CanvasItem(layer, image_path)
                self.items.append(ci)
                self.refresh_tree()
                self.redraw_canvas()
                return
    
    def remove_from_canvas_by_file(self, filename):
        new_items = [ci for ci in self.items if ci.layer.get("file") != filename]
        if len(new_items) != len(self.items):
            self.items = new_items
            for l in self.model.get("layers", []):
                if l.get("file") == filename:
                    l["_selected"] = False
                    
            self.refresh_tree()
            self.redraw_canvas()
    
    def delete_file(self, filename):
        if messagebox.askyesno("Удаление файла", f"Удалить {filename} навсегда?"):
            self.remove_from_canvas_by_file(filename)
            self.imported_files = [f for f in self.imported_files if f[0] != filename]
            if self.model_dir:
                file_path = os.path.join(self.model_dir, filename)
                if os.path.exists(file_path):
                    os.remove(file_path)
            self.model["layers"] = [l for l in self.model["layers"] if l.get("file") != filename]
            self.refresh_import_list()
            self.refresh_tree()
            self.redraw_canvas()
            logger.info(f"File deleted: {filename}")
    
    def show_blink_preview(self):
        if not self.selected_group:
            return
            
        gname = self.selected_group
        group = next((g for g in self.model.get("groups", []) if g.get("name") == gname), None)
        if not group:
            return
            
        blink_freq = float(self.blink_freq.get())
        if blink_freq < 0.1:
            return
            
        self.blink_preview_running = True
        self._blink_preview_loop()
        logger.info(f"Blink preview started for group {gname}")
    
    def stop_blink_preview(self):
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
    
    def export_zip(self):
        try:
            from utils import export_model_zip
        except Exception as e:
            messagebox.showerror("Ошибка экспорта", f"Не удалось импортировать утилиту экспорта: {e}")
            logger.error(f"Error importing export utility: {e}")
            return
            
        if not self.model_dir:
            messagebox.showwarning("Нет модели", "Сначала сохраните или импортируйте изображения")
            return
            
        try:
            zip_path = export_model_zip(self.model, self.model_dir)
            messagebox.showinfo("Экспортировано", f"Модель экспортирована: {zip_path}")
            logger.info(f"Model exported to: {zip_path}")
        except Exception as e:
            import traceback
            tb = traceback.format_exc()
            with open("export_zip_error.log", "w", encoding="utf-8") as f:
                f.write(tb)
                
            messagebox.showerror("Ошибка экспорта", f"Ошибка при экспорте: {e}. Смотри export_zip_error.log")
            logger.error(f"Error exporting model: {e}\n{tb}")
    
    def _preview_loop(self):
        try:
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
                                "parent": next((g.get("parent") for g in self.model.get("groups", []) if g.get("name") == ci.layer.get("group")), None),
                                "flip_horizontal": bool(ci.flip_horizontal),
                                "flip_vertical": bool(ci.flip_vertical)
                            })
                        with open(os.path.join(self.model_dir, "model.json"), "w", encoding="utf-8") as f:
                            json.dump(temp, f, indent=2, ensure_ascii=False)
                except Exception as e:
                    logger.error(f"Error autosaving: {e}")
                self.last_autosave = now
                
            mode = self.test_mode_var.get()
            level = 0.0
            
            if mode == "microphone":
                level = self.audio_level
            elif mode == "none":
                level = 0.0
                
            self.redraw_canvas(level, mode)
        except Exception as e:
            logger.error(f"Error in preview loop: {e}")
            import traceback
            logger.error(traceback.format_exc())
        finally:
            if self.winfo_exists():
                self.after(int(1000 / self.preview_fps), self._preview_loop)