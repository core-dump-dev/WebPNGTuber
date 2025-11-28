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

# Определение базовой директории
if getattr(sys, 'frozen', False):
    BASE_DIR = os.path.dirname(sys.executable)
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))

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
        
        # Загружаем видимость из модели, но для слоев моргания принудительно устанавливаем невидимыми
        self.visible = bool(layer.get("visible", True))
        if layer.get("blink", False) or "blink" in layer.get("name", "").lower():
            self.visible = False
        
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
                # Для GIF загружаем первый кадр как оригинальное изображение
                with Image.open(self.image_path) as gif:
                    gif.seek(0)
                    self.original_image = gif.copy().convert("RGBA")
            else:
                self.original_image = Image.open(self.image_path).convert("RGBA")
        except Exception as e:
            print(f"Ошибка загрузки изображения: {e}")
            # Создаем пустое изображение в случае ошибки
            self.original_image = Image.new("RGBA", (100, 100), (255, 0, 0, 128))

    def apply_transformations(self, img):
        """Применяет масштаб, поворот и отражение к изображению"""
        if not img:
            return img
            
        # Создаем копию для трансформаций
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
                print(f"Ошибка обновления GIF: {e}")
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
    def __init__(self, master, on_save=None, device='По умолчанию', noise_gate_enabled=True, sensitivity=1.0, thresholds=None):
        super().__init__(master)
        self.title("Редактор моделей")
        self.geometry("1400x800")
        self.on_save = on_save
        self.protocol("WM_DELETE_WINDOW", self.on_close)

        # Сохраняем настройки микрофона
        self.mic_device = device
        self.mic_noise_gate_enabled = noise_gate_enabled
        self.mic_sensitivity = sensitivity
        self.thresholds = thresholds or {
            'silent': 0.05,
            'whisper': 0.25,
            'normal': 0.6,
            'shout': 0.8
        }

        # Данные модели
        self.model = {"name": "Без названия", "layers": [], "groups": []}
        self.model_dir = None
        self.original_slot = None
        self.items = []
        self.imported_files = []
        self.drag_data = {"item": None, "x": 0, "y": 0}
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
        ttk.Radiobutton(test_frame, text="Симуляция", variable=self.test_mode_var, 
                       value="simulate", command=self.update_test_mode).pack(anchor="w")
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
        self.audio_processor.noise_gate_threshold = 0.01 if self.mic_noise_gate_enabled else 0.0
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
        
        # Список элементов
        items_list_frame = ttk.Frame(items_frame)
        items_list_frame.pack(fill="both", expand=True, pady=(0, 5))
        
        self.items_listbox = tk.Listbox(items_list_frame, selectmode="extended", height=15, exportselection=False)
        scrollbar = ttk.Scrollbar(items_list_frame, orient="vertical", command=self.items_listbox.yview)
        self.items_listbox.configure(yscrollcommand=scrollbar.set)
        
        scrollbar.pack(side="right", fill="y")
        self.items_listbox.pack(side="left", fill="both", expand=True)
        self.items_listbox.bind("<<ListboxSelect>>", self.on_list_select)

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
        logic_frame = ttk.LabelFrame(groups_frame, text="Состояние → Слой")
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
            
            ttk.Label(row, text=states[s] + ":", width=8).pack(side="left")
            
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
        except Exception:
            pass

        # Запуск превью
        self.after(100, self._preview_loop)

    def on_mirror_change(self):
        """Обработка изменения зеркалирования"""
        if len(self.current_selection) == 1:
            ci = self.current_selection[0]
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
            
            self.zoom_reset()
        except Exception as e:
            messagebox.showerror("Ошибка", f"Неверный размер холста: {e}")

    def on_close(self):
        """Обработка закрытия окна"""
        try:
            self.audio_processor.stop()
            self.stop_blink_preview()
        except Exception as e:
            print("Ошибка остановки аудио:", e)
        
        self.cleanup_temp_folders()
        self.grab_release()
        self.destroy()

    def cleanup_temp_folders(self):
        """Удаление всех временных папок в models"""
        temp_folders = glob.glob(os.path.join(MODELS_DIR, "temp_*"))
        for folder in temp_folders:
            try:
                if os.path.isdir(folder):
                    shutil.rmtree(folder)
            except Exception:
                pass

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
                self.audio_processor.noise_gate_threshold = 0.01 if self.mic_noise_gate_enabled else 0.0
                self.audio_processor.set_sensitivity(self.mic_sensitivity)
                self.audio_processor.start()
            except Exception as e:
                print("Ошибка аудиопроцессора:", e)
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

    # ---------------- Управление моделями ----------------
    def new_model(self):
        name = simpledialog.askstring("Имя модели", "Введите имя модели", parent=self)
        if not name:
            return
        self.model = {"name": name, "layers": [], "groups": []}
        self.model_dir = None
        self.original_slot = None
        self.items.clear()
        self.imported_files.clear()
        self.refresh_import_list()
        self.refresh_items_list()
        self.zoom_reset()
        self.redraw_canvas()

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
                print("Ошибка загрузки изображения", e)
                
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
                except Exception:
                    pass
                    
        self.refresh_import_list()
        self.refresh_items_list()
        self.zoom_reset()
        self.redraw_canvas()

    def save_model(self):
        if not self.model_dir:
            name = self.model.get("name", "model")
            folder = filedialog.askdirectory(title="Выберите папку для модели")
            if not folder:
                return
            self.model_dir = os.path.join(folder, name.replace(" ", "_"))
            os.makedirs(self.model_dir, exist_ok=True)
        
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
            except Exception:
                pass
        
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
            except Exception:
                pass
            self.model_dir = None

    def create_preview(self):
        if not self.model_dir:
            return
            
        base = Image.new("RGBA", (self.canvas_width, self.canvas_height), (0, 0, 0, 0))
        center_x = self.canvas_width // 2
        center_y = self.canvas_height // 2
        for ci in self.items:
            if not ci.visible:
                continue
                
            img = ci.get_current_image()
            if not img:
                continue
                
            px = center_x - img.size[0] // 2 + int(ci.x)
            py = center_y - img.size[1] // 2 + int(ci.y)
            try:
                base.alpha_composite(img, (px, py))
            except Exception:
                pass
                
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
                print("Ошибка импорта", e)
                
        self.refresh_import_list()
        self.refresh_items_list()
        self.redraw_canvas()
        self.last_autosave = time.time()

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

    def refresh_items_list(self):
        self.items_listbox.delete(0, "end")
        groups = self.model.get("groups", [])
        for g in groups:
            name = g.get("name", "(группа)")
            self.items_listbox.insert("end", f"[Группа] {name}")
        
        for i, ci in enumerate(reversed(self.items)):
            layer = ci.layer
            name = layer.get("name", f"слой{i}")
            grp = layer.get("group")
            
            state_info = ""
            if grp:
                group_obj = next((g for g in self.model["groups"] if g["name"] == grp), None)
                if group_obj:
                    for state, child in group_obj.get("logic", {}).items():
                        if child == name:
                            state_info = f" @ {grp} {{{state}}}"
                            break
            
            visible_flag = "✔" if ci.visible else "✘"
            flags = []
            if layer.get("blink"):
                flags.append("моргание")
            if ci.is_gif:
                flags.append("GIF")
            if ci.flip_horizontal:
                flags.append("гор.зерк")
            if ci.flip_vertical:
                flags.append("верт.зерк")
                
            flag_text = f" ({','.join(flags)})" if flags else ""
            label = f"{visible_flag} {name}{flag_text}{state_info}"
            self.items_listbox.insert("end", label)

    def _get_current_state_for_group(self, group_name, current_state):
        """Определяет текущее состояние для группы с учетом всех эффектов"""
        group = next((g for g in self.model.get("groups", []) if g.get("name") == group_name), None)
        if not group:
            return None
            
        logic = group.get("logic", {})
        
        # Обработка моргания
        if self.test_mode_var.get() != "none":
            now = time.time()
            blink_freq = float(group.get("blink_freq", 0.0))
            
            if group_name not in self.group_blink_timers:
                self.group_blink_timers[group_name] = now + random.uniform(2.0, 6.0)
                self.group_blink_until[group_name] = 0.0
                
            if blink_freq > 0.001:
                if now > self.group_blink_timers.get(group_name, 0):
                    self.group_blink_until[group_name] = now + 0.12
                    self.group_blink_timers[group_name] = now + blink_freq
                
                if now < self.group_blink_until.get(group_name, 0):
                    if "blink" in logic:
                        return logic["blink"]
        
        # Обработка случайного эффекта
        if group.get("random_effect", False) and self.random_effect_var.get():
            now = time.time()
            min_time = group.get("random_min", 5.0)
            max_time = group.get("random_max", 10.0)
            
            if now > self.group_random_timers.get(group_name, 0):
                children = group.get("children", [])
                if children:
                    blink_layer = logic.get("blink", "")
                    open_layer = logic.get("open", "")
                    available = [c for c in children if c != blink_layer and c != open_layer]
                    
                    if available:
                        chosen = random.choice(available)
                        self.group_random_current[group_name] = chosen
                
                interval = random.uniform(min_time, max_time)
                self.group_random_timers[group_name] = now + interval
            
            if self.group_random_current.get(group_name):
                return self.group_random_current.get(group_name)
        
        # Голосовые состояния
        if current_state in logic:
            return logic.get(current_state)
        
        return logic.get("open") or logic.get("normal") or logic.get("whisper") or logic.get("silent")

    def redraw_canvas(self, level=0.0, mode="none"):
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
        
        if mode == "none":
            # Режим редактирования - все видимые слои
            for ci in self.items:
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
                    print(f"Ошибка композиции: {e}")
        else:
            # Режим тестирования - полная логика как в рендерере
            current_state = "silent"
            if level > self.thresholds['shout']:
                current_state = "shout"
            elif level > self.thresholds['normal']:
                current_state = "normal"
            elif level > self.thresholds['whisper']:
                current_state = "whisper"
            elif level > self.thresholds['silent']:
                current_state = "silent"
            
            # Собираем выборки для всех групп
            group_choices = {}
            for group in self.model.get("groups", []):
                chosen = self._get_current_state_for_group(group['name'], current_state)
                if chosen:
                    group_choices[group['name']] = chosen
            
            # Отрисовываем слои с учетом логики групп
            for ci in self.items:
                if not ci.visible:
                    continue
                    
                group_name = ci.layer.get("group")
                if group_name and group_name in group_choices:
                    if ci.layer.get("name") != group_choices[group_name]:
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
                    print(f"Ошибка композиции: {e}")
        
        # Конвертируем изображение в PhotoImage и отображаем
        try:
            self.canvas_image = ImageTk.PhotoImage(temp_image)
            self.canvas.create_image(canvas_x1, canvas_y1, anchor="nw", image=self.canvas_image)
        except Exception as e:
            print(f"Ошибка отображения: {e}")
        
        # Выделение выбранных элементов
        if self.selected_group:
            for ci in self.items:
                if ci.layer.get("group") == self.selected_group:
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
                        outline="orange", width=2
                    )
        else:
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
                self.refresh_items_list()
                self.redraw_canvas()
                return

    def remove_from_canvas_by_file(self, filename):
        new_items = [ci for ci in self.items if ci.layer.get("file") != filename]
        if len(new_items) != len(self.items):
            self.items = new_items
            for l in self.model.get("layers", []):
                if l.get("file") == filename:
                    l["_selected"] = False
            self.refresh_items_list()
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
            self.refresh_items_list()
            self.redraw_canvas()

    def on_list_select(self, event=None):
        sels = list(self.items_listbox.curselection())
        self.current_selection = []
        
        if not sels:
            for c in self.items:
                c.layer["_selected"] = False
            self.selected_group = None
            self.group_label.config(text="(нет группы)")
            self.name_entry.delete(0, "end")
            self.x_entry.delete(0, "end")
            self.y_entry.delete(0, "end")
            self.scale_entry.delete(0, "end")
            self.rotation_entry.delete(0, "end")
            self.flip_h_var.set(False)
            self.flip_v_var.set(False)
            self.visible_var.set(True)
            self.redraw_canvas()
            return

        total_groups = len(self.model.get("groups", []))
        first_sel = sels[0]
        if first_sel < total_groups:
            # Выбрана группа
            try:
                grp = self.model.get("groups", [])[first_sel]
                gname = grp.get("name")
                self.selected_group = gname
                for ci in self.items:
                    ci.layer["_selected"] = (ci.layer.get("group") == gname)
                self.group_label.config(text=gname)
                children = [ci.layer.get("name") for ci in self.items if ci.layer.get("group") == gname]
                
                # Обновляем выпадающие меню для состояний
                for state in ("silent", "whisper", "normal", "shout", "blink", "open"):
                    om = getattr(self, f"{state}_menu")
                    menu = om["menu"]
                    menu.delete(0, "end")
                    menu.add_command(label="", command=lambda v=self.state_vars[state]: v.set(""))
                    
                    for child in children:
                        menu.add_command(
                            label=f"Слой: {child}",
                            command=lambda val=child, v=self.state_vars[state]: v.set(val)
                        )
                    
                    for group in self.model.get("groups", []):
                        if group["name"] != gname:
                            menu.add_command(
                                label=f"Группа: {group['name']}",
                                command=lambda val=group['name'], v=self.state_vars[state]: v.set(val)
                            )
                
                # Загружаем сохраненную логику
                saved_logic = grp.get("logic", {})
                for state in saved_logic:
                    if state in self.state_vars:
                        self.state_vars[state].set(saved_logic[state])
                
                self.random_effect_var.set(grp.get("random_effect", False))
                self.random_min_var.set(grp.get("random_min", 5.0))
                self.random_max_var.set(grp.get("random_max", 10.0))
                self.blink_freq.set(float(grp.get("blink_freq", 0.0)))
                self.blink_freq_entry.delete(0, "end")
                self.blink_freq_entry.insert(0, str(self.blink_freq.get()))
                
                # Очищаем поля свойств элемента
                self.name_entry.delete(0, "end")
                self.x_entry.delete(0, "end")
                self.y_entry.delete(0, "end")
                self.scale_entry.delete(0, "end")
                self.rotation_entry.delete(0, "end")
                self.flip_h_var.set(False)
                self.flip_v_var.set(False)
                self.visible_var.set(True)
            except Exception as e:
                print(f"Ошибка при выборе группы: {e}")
                messagebox.showerror("Ошибка", f"Не удалось загрузить группу: {e}")
        else:
            # Выбраны элементы
            self.selected_group = None
            sel_layers = set()
            total = len(self.items)
            for s in sels:
                idx = s - total_groups
                idx = total - 1 - idx
                if 0 <= idx < total:
                    sel_layers.add(self.items[idx])
            for ci in self.items:
                ci.layer["_selected"] = (ci in sel_layers)
            sel = list(sel_layers)
            self.current_selection = sel
            
            if sel:
                if len(sel) == 1:
                    first = sel[0]
                    self.name_entry.delete(0, "end")
                    self.name_entry.insert(0, first.layer.get("name", ""))
                    self.x_entry.delete(0, "end")
                    self.x_entry.insert(0, str(first.x))
                    self.y_entry.delete(0, "end")
                    self.y_entry.insert(0, str(first.y))
                    self.scale_entry.delete(0, "end")
                    self.scale_entry.insert(0, str(first.scale))
                    self.rotation_entry.delete(0, "end")
                    self.rotation_entry.insert(0, str(first.rotation))
                    self.flip_h_var.set(bool(first.flip_horizontal))
                    self.flip_v_var.set(bool(first.flip_vertical))
                    self.visible_var.set(bool(first.visible))
                else:
                    self.name_entry.delete(0, "end")
                    self.x_entry.delete(0, "end")
                    self.y_entry.delete(0, "end")
                    self.scale_entry.delete(0, "end")
                    self.rotation_entry.delete(0, "end")
                    self.flip_h_var.set(False)
                    self.flip_v_var.set(False)
                    self.visible_var.set(True)
            self.group_label.config(text="(нет группы)")
        self.redraw_canvas()

    def apply_props_from_entry(self, event=None):
        """Применение свойств при нажатии Enter в поле ввода"""
        self.apply_props()

    def apply_props(self):
        if not self.current_selection:
            messagebox.showwarning("Нет выбора", "Сначала выберите элемент")
            return
            
        if len(self.current_selection) > 1:
            messagebox.showwarning("Множественный выбор", "Свойства можно применять только к одному элементу")
            return
            
        ci = self.current_selection[0]
        name = self.name_entry.get().strip()
        try:
            x = int(self.x_entry.get().strip())
            y = int(self.y_entry.get().strip())
            scale = float(self.scale_entry.get().strip())
            rotation = int(self.rotation_entry.get().strip())
        except Exception:
            messagebox.showwarning("Ошибка", "X и Y должны быть целыми числами, масштаб - дробным, поворот - целым")
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
        
        if need_redraw:
            self.redraw_canvas()
        
        self.refresh_items_list()
        self.last_autosave = time.time()

    def bring_forward(self):
        if not self.current_selection:
            return
            
        for ci in self.current_selection:
            idx = self.items.index(ci)
            if idx < len(self.items) - 1:
                self.items[idx], self.items[idx + 1] = self.items[idx + 1], self.items[idx]
        self.refresh_items_list()
        self.redraw_canvas()

    def send_backward(self):
        if not self.current_selection:
            return
            
        for ci in self.current_selection:
            idx = self.items.index(ci)
            if idx > 0:
                self.items[idx], self.items[idx - 1] = self.items[idx - 1], self.items[idx]
        self.refresh_items_list()
        self.redraw_canvas()

    def group_selected(self):
        if not self.current_selection or len(self.current_selection) < 1:
            messagebox.showwarning("Группа", "Выберите хотя бы один элемент")
            return
            
        name = simpledialog.askstring("Имя группы", "Введите имя группы", parent=self)
        if not name:
            return
        existing = [g.get("name") for g in self.model.get("groups", [])]
        if name in existing:
            messagebox.showwarning("Группа", "Имя группы уже существует")
            return
            
        group = {
            "name": name, 
            "children": [ci.layer.get("name") for ci in self.current_selection], 
            "logic": {}, 
            "blink_freq": 0.0,
            "random_effect": False,
            "random_min": 5.0,
            "random_max": 10.0
        }
        self.model.setdefault("groups", []).append(group)
        for ci in self.current_selection:
            ci.layer["group"] = name
            
        for ci in self.items:
            ci.layer["_selected"] = False
        self.current_selection = []
            
        self.selected_group = name
        self.refresh_items_list()
        self.redraw_canvas()

    def ungroup_selected(self):
        if self.selected_group:
            gname = self.selected_group
            for g in list(self.model.get("groups", [])):
                if g.get("name") == gname:
                    self.model["groups"].remove(g)
            for ci in self.items:
                if ci.layer.get("group") == gname:
                    ci.layer["group"] = None
            self.selected_group = None
            self.refresh_items_list()
            self.redraw_canvas()
            return
            
        if not self.current_selection:
            return
            
        for ci in self.current_selection:
            grp = ci.layer.get("group")
            if grp:
                for g in list(self.model.get("groups", [])):
                    if g.get("name") == grp and ci.layer.get("name") in g.get("children", []):
                        g["children"].remove(ci.layer.get("name"))
                        if not g["children"]:
                            self.model["groups"].remove(g)
                ci.layer["group"] = None
                
        self.refresh_items_list()
        self.redraw_canvas()

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
        
        found = None
        for ci in reversed(self.items):
            if not ci.visible:
                continue
                
            img = ci.get_current_image()
            if not img:
                continue
                
            # Рассчитываем позицию элемента на холсте
            px = (self.canvas_width // 2) - img.width // 2 + ci.x
            py = (self.canvas_height // 2) - img.height // 2 + ci.y
            
            # Проверяем попадание
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
                except Exception:
                    found = ci
                    break
                    
        if found:
            if event.state & 0x0004:  # Ctrl
                found.layer["_selected"] = not bool(found.layer.get("_selected"))
                if found.layer["_selected"]:
                    self.current_selection.append(found)
                else:
                    if found in self.current_selection:
                        self.current_selection.remove(found)
            else:
                for c in self.items:
                    c.layer["_selected"] = False
                self.current_selection = [found]
                found.layer["_selected"] = True
                
            grp = found.layer.get("group")
            if grp:
                self.selected_group = grp
            else:
                self.selected_group = None
                
            self.drag_data["item"] = found
            self.drag_data["x"] = mx
            self.drag_data["y"] = my
            self.on_list_select()
        else:
            for c in self.items:
                c.layer["_selected"] = False
            self.current_selection = []
            self.selected_group = None
            self.drag_data["item"] = None
            self.on_list_select()

    def on_canvas_mouse_move(self, event):
        if not self.drag_data.get("item"):
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
            
        ci = self.drag_data["item"]
        dx = mx - self.drag_data["x"]
        dy = my - self.drag_data["y"]
        self.drag_data["x"] = mx
        self.drag_data["y"] = my
        
        if self.selected_group:
            for s in self.items:
                if s.layer.get("group") == self.selected_group:
                    s.x += int(dx)
                    s.y += int(dy)
        else:
            for s in self.current_selection:
                s.x += int(dx)
                s.y += int(dy)
                
        # Обновляем поля координат в реальном времени
        if len(self.current_selection) == 1:
            self.x_entry.delete(0, "end")
            self.x_entry.insert(0, str(self.current_selection[0].x))
            self.y_entry.delete(0, "end")
            self.y_entry.insert(0, str(self.current_selection[0].y))
                
        self.refresh_items_list()
        self.redraw_canvas()

    def on_canvas_mouse_up(self, event):
        self.drag_data["item"] = None
        self.last_autosave = time.time()

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
            
        logic = {}
        for s, var in self.state_vars.items():
            val = var.get().strip()
            if val:
                logic[s] = val
                
        grp["logic"] = logic
        grp["blink_freq"] = float(self.blink_freq.get())
        
        grp["random_effect"] = self.random_effect_var.get()
        grp["random_min"] = self.random_min_var.get()
        grp["random_max"] = self.random_max_var.get()
        
        messagebox.showinfo("Логика группы", f"Логика для группы {gname} сохранена")

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
        
    def stop_blink_preview(self):
        self.blink_preview_running = False
        
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
        
        for ci in self.items:
            if ci.layer.get("group") == gname:
                ci.visible = False
                if ci.layer.get("name") == blink_layer:
                    ci.visible = True
        
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
        
        for ci in self.items:
            if ci.layer.get("group") == gname:
                ci.visible = False
                if ci.layer.get("name") == open_layer:
                    ci.visible = True
        
        self.redraw_canvas(0, "none")
        
        blink_freq = float(group.get("blink_freq", 0.0))
        if blink_freq > 0.1:
            self.after(int(blink_freq * 1000), self._blink_preview_loop)

    def export_zip(self):
        try:
            from utils import export_model_zip
        except Exception as e:
            messagebox.showerror("Ошибка экспорта", f"Не удалось импортировать утилиту экспорта: {e}")
            return
        if not self.model_dir:
            messagebox.showwarning("Нет модели", "Сначала сохраните или импортируйте изображения")
            return
        try:
            zip_path = export_model_zip(self.model, self.model_dir)
            messagebox.showinfo("Экспортировано", f"Модель экспортирована: {zip_path}")
        except Exception as e:
            import traceback
            tb = traceback.format_exc()
            with open("export_zip_error.log", "w", encoding="utf-8") as f:
                f.write(tb)
            messagebox.showerror("Ошибка экспорта", f"Ошибка при экспорте: {e}. Смотри export_zip_error.log")

    def _preview_loop(self):
        try:
            now = time.time()
            if now - self.last_autosave > self.autosave_interval:
                try:
                    if self.model_dir:
                        temp = {"name": self.model.get("name", ""), "layers": [], "groups": self.model.get("groups", [])}
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
                    print("Ошибка автосохранения", e)
                self.last_autosave = now
            
            mode = self.test_mode_var.get()
            if mode == "microphone":
                level = self.audio_level
            elif mode == "simulate":
                t = time.time()
                # Более реалистичная симуляция с разными состояниями
                if int(t) % 10 < 3:
                    level = 0.1  # Тишина
                elif int(t) % 10 < 6:
                    level = 0.4  # Шёпот
                elif int(t) % 10 < 8:
                    level = 0.7  # Норма
                else:
                    level = 0.9  # Крик
                level = level * self.mic_sensitivity
                self.level_bar["value"] = level * 100
            else:
                level = 0.0
                
            self.redraw_canvas(level, mode)
        except Exception as e:
            print("Ошибка цикла превью", e)
        finally:
            if self.winfo_exists():
                self.after(int(1000 / self.preview_fps), self._preview_loop)