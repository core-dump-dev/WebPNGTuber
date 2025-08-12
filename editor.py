import os
import json
import time
import tkinter as tk
from tkinter import ttk, filedialog, simpledialog, messagebox
from PIL import Image, ImageTk, ImageSequence
import shutil
import math
import random
from audio import AudioProcessor

MODELS_DIR = os.path.join(os.path.dirname(__file__), "models")
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
        self.visible = bool(layer.get("visible", True))
        
        # Инициализация атрибутов для GIF
        self.gif_frames = []
        self.current_frame = 0
        self.last_frame_time = 0
        self.frame_durations = []
        
        # Загрузка изображения с трансформациями
        self.image = None
        self.tkimage = None
        self.update_image()

    def apply_transformations(self, img):
        """Применяет масштаб и поворот к изображению"""
        if self.scale != 1.0:
            new_width = int(img.width * self.scale)
            new_height = int(img.height * self.scale)
            img = img.resize((new_width, new_height), Image.LANCZOS)
        if self.rotation != 0:
            img = img.rotate(self.rotation, expand=True)
        return img

    def update_image(self):
        """Обновляет изображение после изменения трансформаций"""
        if self.is_gif:
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
                print(f"Error loading GIF frames: {e}")
                self.is_gif = False
                # Если не удалось загрузить GIF, используем статическое изображение
                img = Image.open(self.image_path).convert("RGBA")
                self.image = self.apply_transformations(img)
        else:
            img = Image.open(self.image_path).convert("RGBA")
            self.image = self.apply_transformations(img)
        
        # Создаем Tkinter-совместимое изображение
        self.tkimage = ImageTk.PhotoImage(self.get_current_image())

    def get_current_image(self):
        """Возвращает текущий кадр (для GIF) или изображение"""
        if self.is_gif and self.gif_frames:
            now = time.time()
            if now - self.last_frame_time > self.frame_durations[self.current_frame]:
                self.current_frame = (self.current_frame + 1) % len(self.gif_frames)
                self.last_frame_time = now
            return self.gif_frames[self.current_frame]
        return self.image

class ModelEditor(tk.Toplevel):
    def __init__(self, master, on_save=None, device='Default', noise_gate_enabled=True, sensitivity=1.0, thresholds=None):
        super().__init__(master)
        self.title("Model Editor")
        self.geometry("1200x750")
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

        # Model storage
        self.model = {"name": "Untitled", "layers": [], "groups": []}
        self.model_dir = None
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

        # ---- UI layout ----
        # Главный контейнер с сеткой
        main_frame = ttk.Frame(self)
        main_frame.pack(fill=tk.BOTH, expand=True, padx=6, pady=6)
        
        # Настройка сетки
        main_frame.columnconfigure(1, weight=1)  # Центральная колонка растягивается
        main_frame.rowconfigure(0, weight=1)     # Единственная строка растягивается
        
        # Левая панель (уменьшена)
        left = ttk.Frame(main_frame, width=200)
        left.grid(row=0, column=0, sticky="ns", padx=(0, 6), pady=0)
        
        # Центральная панель
        center = ttk.Frame(main_frame)
        center.grid(row=0, column=1, sticky="nsew", padx=0, pady=0)
        
        # Правая панель (уменьшена)
        right = ttk.Frame(main_frame, width=300)
        right.grid(row=0, column=2, sticky="ns", padx=(6, 0), pady=0)

        # ---- Левая панель ----
        ttk.Button(left, text="New Model", command=self.new_model).pack(fill="x", pady=2)
        ttk.Button(left, text="Load Model", command=self.load_model).pack(fill="x", pady=2)
        ttk.Button(left, text="Save Model", command=self.save_model).pack(fill="x", pady=2)
        ttk.Button(left, text="Import PNG/GIF(s)", command=self.import_images).pack(fill="x", pady=2)
        ttk.Button(left, text="Export ZIP", command=self.export_zip).pack(fill="x", pady=2)
        
        # Test mode selector with None option
        test_frame = ttk.LabelFrame(left, text="Test Mode")
        test_frame.pack(fill="x", pady=10)
        
        self.test_mode_var = tk.StringVar(value="none")
        ttk.Radiobutton(test_frame, text="Simulate", variable=self.test_mode_var, 
                       value="simulate", command=self.update_test_mode).pack(anchor="w")
        ttk.Radiobutton(test_frame, text="Microphone", variable=self.test_mode_var, 
                       value="microphone", command=self.update_test_mode).pack(anchor="w")
        ttk.Radiobutton(test_frame, text="None", variable=self.test_mode_var, 
                       value="none", command=self.update_test_mode).pack(anchor="w")
        
        # Audio level indicator
        self.level_bar = ttk.Progressbar(test_frame, length=180, mode="determinate")
        self.level_bar.pack(fill="x", pady=5)
        
        # Start audio processor with current settings
        self.audio_processor = AudioProcessor(
            callback=self.on_audio_level,
            device=self.mic_device
        )
        self.audio_processor.noise_gate_threshold = 0.01 if self.mic_noise_gate_enabled else 0.0

        ttk.Label(left, text="Imported Images:").pack(anchor="w", pady=(8, 0))
        
        # Контейнер для списка импортированных изображений с прокруткой
        import_frame = ttk.Frame(left)
        import_frame.pack(fill="both", expand=True)
        
        self.import_canvas = tk.Canvas(import_frame, width=180, height=250)
        self.import_vscroll = ttk.Scrollbar(import_frame, orient="vertical", command=self.import_canvas.yview)
        self.import_canvas.configure(yscrollcommand=self.import_vscroll.set)
        
        self.import_vscroll.pack(side="right", fill="y")
        self.import_canvas.pack(side="left", fill="both", expand=True)
        
        self.import_inner = ttk.Frame(self.import_canvas)
        self.import_canvas.create_window((0, 0), window=self.import_inner, anchor="nw")
        self.import_inner.bind("<Configure>", lambda e: self.import_canvas.configure(scrollregion=self.import_canvas.bbox("all")))

        # ---- Центральная панель ----
        preview_frame = ttk.LabelFrame(center, text="Canvas Preview")
        preview_frame.pack(fill="both", expand=True)
        self.canvas_w = 700
        self.canvas_h = 700
        self.canvas = tk.Canvas(preview_frame, width=self.canvas_w, height=self.canvas_h, bg="#222")
        self.canvas.pack(expand=True)
        self.canvas.bind("<ButtonPress-1>", self.on_canvas_mouse_down)
        self.canvas.bind("<B1-Motion>", self.on_canvas_mouse_move)
        self.canvas.bind("<ButtonRelease-1>", self.on_canvas_mouse_up)

        # ---- Правая панель ----
        # Вкладки для лучшей организации
        notebook = ttk.Notebook(right)
        notebook.pack(fill="both", expand=True)
        
        # Вкладка 1: Управление элементами
        items_tab = ttk.Frame(notebook)
        notebook.add(items_tab, text="Items")
        
        # Вкладка 2: Логика групп
        groups_tab = ttk.Frame(notebook)
        notebook.add(groups_tab, text="Group Logic")
        
        # ---- Вкладка "Items" ----
        items_frame = ttk.LabelFrame(items_tab, text="Canvas Items")
        items_frame.pack(fill="both", expand=True, padx=5, pady=5)
        
        # Список элементов с прокруткой
        items_list_frame = ttk.Frame(items_frame)
        items_list_frame.pack(fill="both", expand=True, pady=(0, 5))
        
        self.items_listbox = tk.Listbox(items_list_frame, selectmode="extended", height=15)
        scrollbar = ttk.Scrollbar(items_list_frame, orient="vertical", command=self.items_listbox.yview)
        self.items_listbox.configure(yscrollcommand=scrollbar.set)
        
        scrollbar.pack(side="right", fill="y")
        self.items_listbox.pack(side="left", fill="both", expand=True)
        self.items_listbox.bind("<<ListboxSelect>>", self.on_list_select)

        # Кнопки управления элементами
        btns_frame = ttk.Frame(items_frame)
        btns_frame.pack(fill="x", pady=(0, 10))
        
        ttk.Button(btns_frame, text="↑", width=3, command=self.bring_forward).pack(side="left", padx=2)
        ttk.Button(btns_frame, text="↓", width=3, command=self.send_backward).pack(side="left", padx=2)
        ttk.Button(btns_frame, text="Group", command=self.group_selected).pack(side="left", padx=2, fill="x", expand=True)
        ttk.Button(btns_frame, text="Ungroup", command=self.ungroup_selected).pack(side="left", padx=2, fill="x", expand=True)

        # Свойства выбранного элемента
        props = ttk.LabelFrame(items_frame, text="Selected Properties")
        props.pack(fill="x", pady=(0, 5))
        
        grid_frame = ttk.Frame(props)
        grid_frame.pack(fill="x", padx=5, pady=5)
        
        ttk.Label(grid_frame, text="Name:").grid(row=0, column=0, sticky="w", padx=2, pady=2)
        self.name_entry = ttk.Entry(grid_frame)
        self.name_entry.grid(row=0, column=1, sticky="ew", padx=2, pady=2)
        
        ttk.Label(grid_frame, text="X:").grid(row=1, column=0, sticky="w", padx=2, pady=2)
        self.x_entry = ttk.Entry(grid_frame)
        self.x_entry.grid(row=1, column=1, sticky="ew", padx=2, pady=2)
        
        ttk.Label(grid_frame, text="Y:").grid(row=2, column=0, sticky="w", padx=2, pady=2)
        self.y_entry = ttk.Entry(grid_frame)
        self.y_entry.grid(row=2, column=1, sticky="ew", padx=2, pady=2)
        
        ttk.Label(grid_frame, text="Scale:").grid(row=3, column=0, sticky="w", padx=2, pady=2)
        self.scale_entry = ttk.Entry(grid_frame)
        self.scale_entry.grid(row=3, column=1, sticky="ew", padx=2, pady=2)
        
        ttk.Label(grid_frame, text="Rotation:").grid(row=4, column=0, sticky="w", padx=2, pady=2)
        self.rotation_entry = ttk.Entry(grid_frame)
        self.rotation_entry.grid(row=4, column=1, sticky="ew", padx=2, pady=2)
        
        self.visible_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(props, text="Visible", variable=self.visible_var).pack(anchor="w", padx=5, pady=(0, 5))
        
        ttk.Button(props, text="Apply to Selection", command=self.apply_props).pack(fill="x", padx=5, pady=5)

        # ---- Вкладка "Group Logic" ----
        groups_frame = ttk.LabelFrame(groups_tab, text="Group Logic")
        groups_frame.pack(fill="both", expand=True, padx=5, pady=5)
        
        # Выбранная группа
        group_info_frame = ttk.Frame(groups_frame)
        group_info_frame.pack(fill="x", pady=(0, 10))
        
        ttk.Label(group_info_frame, text="Selected Group:").pack(side="left", padx=(0, 5))
        self.group_label = ttk.Label(group_info_frame, text="(no group)", font=("Arial", 9, "bold"))
        self.group_label.pack(side="left")

        # Настройки логики
        logic_frame = ttk.LabelFrame(groups_frame, text="Map State → Child Layer")
        logic_frame.pack(fill="x", pady=(0, 10))
        
        self.state_vars = {
            "silent": tk.StringVar(value=""),
            "whisper": tk.StringVar(value=""),
            "normal": tk.StringVar(value=""),
            "shout": tk.StringVar(value=""),
            "blink": tk.StringVar(value=""),
            "open": tk.StringVar(value="")
        }
        
        # Сетка для настроек состояний
        for i, s in enumerate(("silent", "whisper", "normal", "shout", "blink", "open")):
            row = ttk.Frame(logic_frame)
            row.pack(fill="x", padx=5, pady=2)
            
            ttk.Label(row, text=s.capitalize() + ":", width=8).pack(side="left")
            
            # Создаем OptionMenu с пустым значением по умолчанию
            om = ttk.OptionMenu(row, self.state_vars[s], "")
            om.pack(side="left", fill="x", expand=True)
            
            # Сохраняем ссылку на меню для последующего обновления
            setattr(self, f"{s}_menu", om)
        
        # Настройки случайного эффекта
        random_frame = ttk.LabelFrame(groups_frame, text="Random Effect")
        random_frame.pack(fill="x", pady=(0, 10))
        
        self.random_effect_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(random_frame, text="Enable Random State Switching", 
                        variable=self.random_effect_var).pack(anchor="w", padx=5, pady=2)
        
        interval_frame = ttk.Frame(random_frame)
        interval_frame.pack(fill="x", padx=5, pady=2)
        
        ttk.Label(interval_frame, text="Interval:").pack(side="left")
        self.random_min_var = tk.DoubleVar(value=5.0)
        ttk.Entry(interval_frame, textvariable=self.random_min_var, width=5).pack(side="left", padx=2)
        ttk.Label(interval_frame, text="to").pack(side="left")
        self.random_max_var = tk.DoubleVar(value=10.0)
        ttk.Entry(interval_frame, textvariable=self.random_max_var, width=5).pack(side="left", padx=2)
        ttk.Label(interval_frame, text="sec").pack(side="left")

        # Настройки моргания
        blink_frame = ttk.LabelFrame(groups_frame, text="Blink Settings")
        blink_frame.pack(fill="x", pady=(0, 10))
        
        freq_frame = ttk.Frame(blink_frame)
        freq_frame.pack(fill="x", padx=5, pady=2)
        
        ttk.Label(freq_frame, text="Frequency:").pack(side="left")
        self.blink_freq = tk.DoubleVar(value=0.0)
        self.blink_freq_entry = ttk.Entry(freq_frame, width=6)
        self.blink_freq_entry.pack(side="left", padx=2)
        self.blink_freq_entry.insert(0, "0.0")
        self.blink_freq_entry.bind("<Return>", self.update_blink_freq_from_entry)
        ttk.Label(freq_frame, text="sec (0=off)").pack(side="left", padx=(5, 0))
        
        btn_frame = ttk.Frame(blink_frame)
        btn_frame.pack(fill="x", padx=5, pady=(0, 5))
        
        ttk.Button(btn_frame, text="Preview", width=8, command=self.show_blink_preview).pack(side="left", padx=2)
        ttk.Button(btn_frame, text="Stop", width=8, command=self.stop_blink_preview).pack(side="left", padx=2)

        # Кнопка применения
        ttk.Button(groups_frame, text="Apply Group Logic", command=self.apply_group_logic).pack(fill="x", pady=10)

        # Start preview loop
        self.after(100, self._preview_loop)

    def on_close(self):
        """Handle window close event"""
        try:
            self.audio_processor.stop()
            self.stop_blink_preview()
        except Exception as e:
            print("Error stopping audio processor:", e)
        
        # Правильно закрываем окно редактора
        self.grab_release()  # Освобождаем захват фокуса
        self.destroy()  # Закрываем только окно редактора
        
        # Показываем главное окно, если оно скрыто
        if hasattr(self.master, 'deiconify'):
            self.master.deiconify()  # Show main window again

    def update_test_mode(self):
        """Update test mode based on selection"""
        mode = self.test_mode_var.get()
        if mode == "microphone":
            try:
                # Перезапускаем процессор с актуальными настройками
                self.audio_processor.stop()
                self.audio_processor = AudioProcessor(
                    callback=self.on_audio_level,
                    device=self.mic_device
                )
                self.audio_processor.noise_gate_threshold = 0.01 if self.mic_noise_gate_enabled else 0.0
                self.audio_processor.start()
            except Exception as e:
                print("Error restarting audio processor:", e)
        else:
            self.audio_processor.stop()
            if mode == "none":
                # Reset level for None mode
                self.audio_level = 0.0
                self.level_bar["value"] = 0

    def on_audio_level(self, level):
        """Callback for audio level updates with sensitivity applied"""
        # Apply sensitivity setting
        level_scaled = level * self.mic_sensitivity
        if self.test_mode_var.get() == "microphone":
            self.level_bar["value"] = level_scaled * 100
        self.audio_level = level_scaled

    # ---------------- Model management ----------------
    def new_model(self):
        name = simpledialog.askstring("Model name", "Enter model name", parent=self)
        if not name:
            return
        self.model = {"name": name, "layers": [], "groups": []}
        self.model_dir = None
        self.items.clear()
        self.imported_files.clear()
        self.refresh_import_list()
        self.refresh_items_list()
        self.redraw_canvas()

    def load_model(self):
        # Show slot selection dialog
        slot_dialog = tk.Toplevel(self)
        slot_dialog.title("Load from Slot")
        slot_dialog.geometry("300x200")
        slot_dialog.transient(self)
        slot_dialog.grab_set()
        
        ttk.Label(slot_dialog, text="Select a slot to load:").pack(pady=10)
        
        for i in range(1, 7):
            slot_dir = os.path.join(MODELS_DIR, f"slot{i}")
            json_path = os.path.join(slot_dir, "model.json")
            
            if os.path.exists(json_path):
                btn_text = f"Slot {i} (has model)"
            else:
                btn_text = f"Slot {i} (empty)"
                
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
            messagebox.showerror("Error", "model.json not found in selected slot")
            return
        with open(json_path, "r", encoding="utf-8") as f:
            self.model = json.load(f)
        self.model_dir = path
        self.items.clear()
        for layer in self.model.get("layers", []):
            fp = os.path.join(self.model_dir, layer.get("file") or "")
            if not os.path.exists(fp):
                continue
            try:
                # Убедимся, что путь установлен
                if "path" not in layer:
                    layer["path"] = self.model_dir
                    
                # Проверяем, является ли файл GIF
                with Image.open(fp) as img:
                    is_gif = img.format == "GIF" and img.is_animated
                    img.seek(0)
                    preview_img = img.copy().convert("RGBA")
                
                ci = CanvasItem(layer, fp)
                self.items.append(ci)
            except Exception as e:
                print("Load image error", e)
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
        self.redraw_canvas()

    def save_model(self):
        # Если модель ещё не сохранена, запрашиваем папку
        if not self.model_dir:
            name = self.model.get("name", "model")
            folder = filedialog.askdirectory(title="Choose parent folder for model (a new folder will be created)")
            if not folder:
                return
            self.model_dir = os.path.join(folder, name.replace(" ", "_"))
            os.makedirs(self.model_dir, exist_ok=True)
        
        # Сохраняем изображения
        for idx, ci in enumerate(self.items):
            layer = ci.layer
            fname = layer.get("file")
            if not fname or not os.path.exists(os.path.join(self.model_dir, fname)):
                if not fname:
                    fname = f"layer_{idx}.png"
                    layer["file"] = fname
                # Сохраняем оригинальное изображение (без трансформаций)
                if ci.is_gif:
                    # Для GIF просто копируем исходный файл
                    shutil.copy2(ci.image_path, os.path.join(self.model_dir, fname))
                else:
                    # Для PNG сохраняем как есть
                    img = Image.open(ci.image_path).convert("RGBA")
                    img.save(os.path.join(self.model_dir, fname))
        
        # Сохраняем структуру модели
        self.model["layers"] = []
        for ci in self.items:
            layer = ci.layer
            layer["x"] = int(ci.x)
            layer["y"] = int(ci.y)
            layer["visible"] = bool(ci.visible)
            layer["is_gif"] = ci.is_gif
            layer["scale"] = float(ci.scale)
            layer["rotation"] = int(ci.rotation)
            self.model["layers"].append(layer)
        
        with open(os.path.join(self.model_dir, "model.json"), "w", encoding="utf-8") as f:
            json.dump(self.model, f, indent=2, ensure_ascii=False)
        
        # Создаем превью
        self.create_preview()
        
        # Всегда предлагаем сохранить в слот
        self.show_save_slot_dialog()
        
        if self.on_save:
            self.on_save(self.model, self.model_dir)
        self.last_autosave = time.time()
    
    def show_save_slot_dialog(self):
        """Show slot selection dialog with buttons for saving"""
        slot_dialog = tk.Toplevel(self)
        slot_dialog.title("Save to Slot")
        slot_dialog.geometry("300x250")
        slot_dialog.transient(self)
        slot_dialog.grab_set()
        
        ttk.Label(slot_dialog, text="Select a slot to save to:").pack(pady=10)
        
        # Фрейм для кнопок
        slots_frame = ttk.Frame(slot_dialog)
        slots_frame.pack(fill="both", expand=True, padx=10, pady=5)
        
        for i in range(1, 7):
            slot_dir = os.path.join(MODELS_DIR, f"slot{i}")
            json_path = os.path.join(slot_dir, "model.json")
            
            if os.path.exists(json_path):
                btn_text = f"Slot {i} (overwrite)"
            else:
                btn_text = f"Slot {i} (new)"
                
            btn = ttk.Button(
                slots_frame, 
                text=btn_text,
                width=20,
                command=lambda i=i: self._save_slot(i, slot_dialog)
            )
            btn.pack(fill="x", padx=10, pady=3)
        
        # Кнопка отмены
        ttk.Button(
            slot_dialog, 
            text="Cancel", 
            command=slot_dialog.destroy
        ).pack(fill="x", padx=20, pady=10)
    
    def _save_slot(self, slot_num, dialog):
        """Save model to selected slot and close dialog"""
        dialog.destroy()
        slot_dir = os.path.join(MODELS_DIR, f"slot{slot_num}")
        os.makedirs(slot_dir, exist_ok=True)
        
        # Копируем все файлы модели в слот
        for fname, _ in self.imported_files:
            src = os.path.join(self.model_dir, fname)
            dst = os.path.join(slot_dir, fname)
            if os.path.exists(src):
                shutil.copy2(src, dst)
        
        # Копируем файл модели и превью
        shutil.copy2(os.path.join(self.model_dir, "model.json"), 
                    os.path.join(slot_dir, "model.json"))
        
        preview_src = os.path.join(self.model_dir, "preview.png")
        preview_dst = os.path.join(slot_dir, "preview.png")
        if os.path.exists(preview_src):
            shutil.copy2(preview_src, preview_dst)
        
        messagebox.showinfo("Saved", f"Model saved to slot {slot_num}")
        
        # Обновляем главное окно
        if hasattr(self.master, 'app') and hasattr(self.master.app, 'refresh_slot_buttons'):
            self.master.app.refresh_slot_buttons()

    def create_preview(self):
        if not self.model_dir:
            return
        base = Image.new("RGBA", (self.canvas_w, self.canvas_h), (0, 0, 0, 0))
        center_x = self.canvas_w // 2
        center_y = self.canvas_h // 2
        for ci in self.items:
            if not ci.visible:
                continue
            img = ci.get_current_image()
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
            title="Select PNG or GIF images", 
            filetypes=[("Images", "*.png *.gif"), ("All", "*.*")]
        )
        if not files:
            return
        if not self.model_dir:
            tmp = os.path.join(MODELS_DIR, f"model_temp_{int(time.time())}")
            os.makedirs(tmp, exist_ok=True)
            self.model_dir = tmp
        for p in files:
            try:
                # Определяем тип файла
                base = os.path.basename(p)
                dest = os.path.join(self.model_dir, base)
                
                # Копируем файл в папку модели
                if os.path.abspath(p) != os.path.abspath(dest):
                    shutil.copy2(p, dest)
                
                # Проверяем, является ли файл анимированным GIF
                is_gif = False
                if base.lower().endswith('.gif'):
                    with Image.open(p) as img:
                        is_gif = img.is_animated
                
                # Создаем превью
                with Image.open(p) as img:
                    img.seek(0)
                    preview_img = img.copy().convert("RGBA")
                
                self.imported_files.append((base, preview_img, is_gif))
                layer = {
                    "name": os.path.splitext(base)[0], 
                    "file": base, 
                    "path": self.model_dir,
                    "blink": False, 
                    "visible": True, 
                    "x": 0, 
                    "y": 0,
                    "scale": 1.0,
                    "rotation": 0,
                    "group": None,
                    "is_gif": is_gif
                }
                self.model.setdefault("layers", []).append(layer)
            except Exception as e:
                print("Import error", e)
        self.refresh_import_list()
        self.last_autosave = time.time()

    # ------------- UI refresh helpers -------------
    def refresh_import_list(self):
        for w in self.import_inner.winfo_children():
            w.destroy()
        for i, (fname, img, is_gif) in enumerate(self.imported_files):
            row = ttk.Frame(self.import_inner)
            row.pack(fill="x", padx=2, pady=2)
            
            # Отображаем иконку GIF для анимированных изображений
            if is_gif:
                icon = "GIF"
            else:
                icon = "PNG"
                
            ttk.Label(row, text=f"{icon}: {fname}", width=15).pack(side="left")
            ttk.Button(row, text="+", width=2, command=lambda f=fname: self.add_to_canvas(f)).pack(side="left", padx=2)
            ttk.Button(row, text="-", width=2, command=lambda f=fname: self.remove_from_canvas_by_file(f)).pack(side="left", padx=2)
            # Кнопка корзины для удаления файла
            ttk.Button(row, text="🗑️", width=2, command=lambda f=fname: self.delete_file(f)).pack(side="left", padx=2)

    def refresh_items_list(self):
        self.items_listbox.delete(0, "end")
        groups = self.model.get("groups", [])
        for g in groups:
            name = g.get("name", "(group)")
            self.items_listbox.insert("end", f"[Group] {name}")
        
        # Добавляем индикатор видимости и информации о группе
        for i, ci in enumerate(reversed(self.items)):
            layer = ci.layer
            name = layer.get("name", f"layer{i}")
            grp = layer.get("group")
            
            # Добавляем информацию о состоянии группы
            state_info = ""
            if grp:
                group_obj = next((g for g in self.model["groups"] if g["name"] == grp), None)
                if group_obj:
                    # Получаем текущее состояние для этого элемента
                    for state, child in group_obj.get("logic", {}).items():
                        if child == name:
                            state_info = f" @ {grp} {{{state}}}"
                            break
            
            # Формируем метку с информацией о видимости
            visible_flag = "✔" if ci.visible else "✘"
            flags = []
            if layer.get("blink"):
                flags.append("blink")
            if ci.is_gif:
                flags.append("GIF")
                
            flag_text = f" ({','.join(flags)})" if flags else ""
            label = f"{visible_flag} {name}{flag_text}{state_info}"
            self.items_listbox.insert("end", label)

    def redraw_canvas(self, level=0.0, mode="none"):
        base = Image.new("RGBA", (self.canvas_w, self.canvas_h), (0, 0, 0, 0))
        center_x = self.canvas_w // 2
        center_y = self.canvas_h // 2
        
        # For "none" mode, show all visible layers
        if mode == "none":
            for ci in self.items:
                if not ci.visible:
                    continue
                
                # Для GIF получаем текущий кадр
                img = ci.get_current_image()
                    
                px = center_x - img.size[0] // 2 + int(ci.x)
                py = center_y - img.size[1] // 2 + int(ci.y)
                try:
                    base.alpha_composite(img, (px, py))
                except Exception:
                    pass
        else:
            # For other modes, use state logic
            current_state = "silent"
            
            if level > self.thresholds['shout']:
                current_state = "shout"
            elif level > self.thresholds['normal']:
                current_state = "normal"
            elif level > self.thresholds['whisper']:
                current_state = "whisper"
            elif level > self.thresholds['silent']:
                current_state = "silent"
            
            # Render layers with state logic
            for ci in self.items:
                if not ci.visible:
                    continue
                    
                # If layer is in a group, check its state
                group_name = ci.layer.get("group")
                if group_name:
                    group = next((g for g in self.model.get("groups", []) if g.get("name") == group_name), None)
                    if group:
                        logic = group.get("logic", {})
                        target_layer = logic.get(current_state) or logic.get("normal") or logic.get("whisper") or logic.get("silent")
                        # Если есть состояние "open", используем его как основное
                        open_layer = logic.get("open")
                        if open_layer and current_state != "blink":
                            target_layer = open_layer
                            
                        if ci.layer.get("name") != target_layer:
                            continue
                
                # Для GIF получаем текущий кадр
                img = ci.get_current_image()
                    
                px = center_x - img.size[0] // 2 + int(ci.x)
                py = center_y - img.size[1] // 2 + int(ci.y)
                try:
                    base.alpha_composite(img, (px, py))
                except Exception:
                    pass
        
        self.base_tk = ImageTk.PhotoImage(base)
        self.canvas.delete("all")
        self.canvas.create_image(0, 0, anchor="nw", image=self.base_tk)
        
        # Draw selection
        if self.selected_group:
            for ci in self.items:
                if ci.layer.get("group") == self.selected_group:
                    img = ci.get_current_image()
                    px = center_x - img.size[0] // 2 + int(ci.x)
                    py = center_y - img.size[1] // 2 + int(ci.y)
                    self.canvas.create_rectangle(px, py, px + img.size[0], py + img.size[1], outline="orange", width=2)
        else:
            for ci in self.items:
                if ci.layer.get("_selected"):
                    img = ci.get_current_image()
                    px = center_x - img.size[0] // 2 + int(ci.x)
                    py = center_y - img.size[1] // 2 + int(ci.y)
                    self.canvas.create_rectangle(px, py, px + img.size[0], py + img.size[1], outline="cyan", width=2)

    # ------------- canvas & items operations -------------
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
                        "path": self.model_dir,  # Добавляем путь
                        "blink": False, 
                        "visible": True, 
                        "x": 0, 
                        "y": 0,
                        "scale": 1.0,
                        "rotation": 0,
                        "group": None,
                        "is_gif": is_gif
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
        """Delete file from model and disk"""
        if messagebox.askyesno("Delete File", f"Delete {filename} permanently?"):
            # Remove from canvas
            self.remove_from_canvas_by_file(filename)
            
            # Remove from imported files
            self.imported_files = [f for f in self.imported_files if f[0] != filename]
            
            # Delete physical file
            if self.model_dir:
                file_path = os.path.join(self.model_dir, filename)
                if os.path.exists(file_path):
                    os.remove(file_path)
            
            # Remove from model layers
            self.model["layers"] = [l for l in self.model["layers"] if l.get("file") != filename]
            
            self.refresh_import_list()
            self.refresh_items_list()
            self.redraw_canvas()

    # ------------- list selection handling -------------
    def on_list_select(self, event=None):
        sels = list(self.items_listbox.curselection())
        self.current_selection = []
        
        if not sels:
            for c in self.items:
                c.layer["_selected"] = False
            self.selected_group = None
            self.group_label.config(text="(no group)")
            # Clear property fields
            self.name_entry.delete(0, "end")
            self.x_entry.delete(0, "end")
            self.y_entry.delete(0, "end")
            self.scale_entry.delete(0, "end")
            self.rotation_entry.delete(0, "end")
            self.visible_var.set(True)
            self.redraw_canvas()
            return

        total_groups = len(self.model.get("groups", []))
        first_sel = sels[0]
        if first_sel < total_groups:
            grp = self.model.get("groups", [])[first_sel]
            gname = grp.get("name")
            self.selected_group = gname
            for ci in self.items:
                ci.layer["_selected"] = (ci.layer.get("group") == gname)
            self.group_label.config(text=gname)
            children = [ci.layer.get("name") for ci in self.items if ci.layer.get("group") == gname]
            
            # ВАЖНОЕ ИСПРАВЛЕНИЕ: Обновляем выпадающие меню для всех состояний
            for state in ("silent", "whisper", "normal", "shout", "blink", "open"):
                # Получаем виджет OptionMenu для этого состояния
                om = getattr(self, f"{state}_menu")
                menu = om["menu"]
                
                # Очищаем текущее меню
                menu.delete(0, "end")
                
                # Добавляем пустой элемент
                menu.add_command(label="", command=lambda v=self.state_vars[state]: v.set(""))
                
                # Добавляем все дочерние элементы группы
                for child in children:
                    menu.add_command(
                        label=child,
                        command=lambda val=child, v=self.state_vars[state]: v.set(val)
                    )
                
                # Восстанавливаем сохраненное значение
                saved_value = grp.get("logic", {}).get(state, "")
                self.state_vars[state].set(saved_value)
            
            # Обновляем остальные настройки группы
            self.random_effect_var.set(grp.get("random_effect", False))
            self.random_min_var.set(grp.get("random_min", 5.0))
            self.random_max_var.set(grp.get("random_max", 10.0))
            self.blink_freq.set(float(grp.get("blink_freq", 0.0)))
            self.blink_freq_entry.delete(0, "end")
            self.blink_freq_entry.insert(0, str(self.blink_freq.get()))
            
            # Clear property fields for objects
            self.name_entry.delete(0, "end")
            self.x_entry.delete(0, "end")
            self.y_entry.delete(0, "end")
            self.scale_entry.delete(0, "end")
            self.rotation_entry.delete(0, "end")
            self.visible_var.set(True)
        else:
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
                # Only show properties for single selection
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
                    self.visible_var.set(bool(first.visible))
                else:
                    # Clear fields for multiple selection
                    self.name_entry.delete(0, "end")
                    self.x_entry.delete(0, "end")
                    self.y_entry.delete(0, "end")
                    self.scale_entry.delete(0, "end")
                    self.rotation_entry.delete(0, "end")
                    self.visible_var.set(True)
            self.group_label.config(text="(no group)")
        self.redraw_canvas()

    # ------------- apply properties -------------
    def apply_props(self):
        if not self.current_selection:
            messagebox.showwarning("No selection", "Select an item first")
            return
            
        if len(self.current_selection) > 1:
            messagebox.showwarning("Multiple selection", "Properties can only be applied to single items")
            return
            
        ci = self.current_selection[0]
        name = self.name_entry.get().strip()
        try:
            x = int(self.x_entry.get().strip())
            y = int(self.y_entry.get().strip())
            scale = float(self.scale_entry.get().strip())
            rotation = int(self.rotation_entry.get().strip())
        except Exception:
            messagebox.showwarning("Invalid", "X and Y must be integers, scale float, rotation integer")
            return
        vis = self.visible_var.get()
        
        if name:
            ci.layer["name"] = name
        ci.x = x
        ci.y = y
        ci.visible = vis
        
        # Apply transformations if changed
        if scale != ci.scale or rotation != ci.rotation:
            ci.scale = scale
            ci.rotation = rotation
            ci.update_image()
        
        self.refresh_items_list()
        self.redraw_canvas()
        self.last_autosave = time.time()

    # ------------- stacking controls -------------
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

    # ------------- grouping -------------
    def group_selected(self):
        if not self.current_selection or len(self.current_selection) < 1:
            messagebox.showwarning("Group", "Select at least one item")
            return
            
        name = simpledialog.askstring("Group name", "Enter group name", parent=self)
        if not name:
            return
        existing = [g.get("name") for g in self.model.get("groups", [])]
        if name in existing:
            messagebox.showwarning("Group", "Group name already exists")
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
            
        # Clear selection
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

    # ------------- canvas mouse handlers -------------
    def on_canvas_mouse_down(self, event):
        mx, my = event.x, event.y
        center_x = self.canvas_w // 2
        center_y = self.canvas_h // 2
        found = None
        for ci in reversed(self.items):
            img = ci.get_current_image()
            px = center_x - img.size[0] // 2 + int(ci.x)
            py = center_y - img.size[1] // 2 + int(ci.y)
            if px <= mx <= px + img.size[0] and py <= my <= py + img.size[1]:
                found = ci
                break
        if found:
            if event.state & 0x0004:  # Ctrl key
                # Toggle selection
                found.layer["_selected"] = not bool(found.layer.get("_selected"))
                if found.layer["_selected"]:
                    self.current_selection.append(found)
                else:
                    if found in self.current_selection:
                        self.current_selection.remove(found)
            else:
                # Single selection
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
            # Clicked on empty space - clear selection
            for c in self.items:
                c.layer["_selected"] = False
            self.current_selection = []
            self.selected_group = None
            self.drag_data["item"] = None
            self.on_list_select()

    def on_canvas_mouse_move(self, event):
        if not self.drag_data.get("item"):
            return
            
        ci = self.drag_data["item"]
        dx = event.x - self.drag_data["x"]
        dy = event.y - self.drag_data["y"]
        self.drag_data["x"] = event.x
        self.drag_data["y"] = event.y
        
        if self.selected_group:
            for s in self.items:
                if s.layer.get("group") == self.selected_group:
                    s.x += dx
                    s.y += dy
        else:
            # Move all selected items
            for s in self.current_selection:
                s.x += dx
                s.y += dy
                
        self.refresh_items_list()
        self.redraw_canvas()

    def on_canvas_mouse_up(self, event):
        self.drag_data["item"] = None
        self.last_autosave = time.time()

    # ------------- group logic apply -------------
    def apply_group_logic(self):
        if not self.selected_group:
            messagebox.showwarning("No group", "Select a group first")
            return
            
        gname = self.selected_group
        grp = None
        for g in self.model.get("groups", []):
            if g.get("name") == gname:
                grp = g
                break
                
        if not grp:
            messagebox.showerror("Error", "Group not found")
            return
            
        logic = {}
        for s, var in self.state_vars.items():
            val = var.get().strip()
            if val:
                logic[s] = val
                
        grp["logic"] = logic
        grp["blink_freq"] = float(self.blink_freq.get())
        
        # Сохраняем настройки случайного эффекта
        grp["random_effect"] = self.random_effect_var.get()
        grp["random_min"] = self.random_min_var.get()
        grp["random_max"] = self.random_max_var.get()
        
        messagebox.showinfo("Group logic", f"Saved logic for group {gname}")

    # ------------- blink preview -------------
    def show_blink_preview(self):
        """Show blink preview animation"""
        if not self.selected_group:
            return
            
        gname = self.selected_group
        group = next((g for g in self.model.get("groups", []) if g.get("name") == gname), None)
        if not group:
            return
            
        blink_freq = float(self.blink_freq.get())
        if blink_freq < 0.1:
            return
            
        # Создаем анимацию моргания
        self.blink_preview_running = True
        self._blink_preview_loop()
        
    def stop_blink_preview(self):
        """Stop blink preview animation"""
        self.blink_preview_running = False
        
    def _blink_preview_loop(self):
        if not self.blink_preview_running:
            return
            
        # Получаем настройки группы
        gname = self.selected_group
        group = next((g for g in self.model.get("groups", []) if g.get("name") == gname), None)
        if not group:
            return
            
        blink_freq = float(group.get("blink_freq", 0.0))
        if blink_freq < 0.1:
            return
            
        # Показываем состояние "blink"
        logic = group.get("logic", {})
        blink_layer = logic.get("blink", "")
        
        # Обновляем состояние для предпросмотра
        for ci in self.items:
            if ci.layer.get("group") == gname:
                # Скрываем все слои группы
                ci.visible = False
                
                # Показываем только слой для моргания
                if ci.layer.get("name") == blink_layer:
                    ci.visible = True
        
        self.redraw_canvas(0, "none")
        
        # Ждем 0.2 секунды (длительность моргания)
        self.after(200, self._show_normal_preview)
        
    def _show_normal_preview(self):
        if not self.blink_preview_running:
            return
            
        # Показываем нормальное состояние
        gname = self.selected_group
        group = next((g for g in self.model.get("groups", []) if g.get("name") == gname), None)
        if not group:
            return
            
        logic = group.get("logic", {})
        # Используем состояние "open" если оно определено, иначе "normal"
        open_layer = logic.get("open") or logic.get("normal") or logic.get("whisper") or logic.get("silent")
        
        for ci in self.items:
            if ci.layer.get("group") == gname:
                # Скрываем все слои группы
                ci.visible = False
                
                # Показываем только слой для открытого состояния
                if ci.layer.get("name") == open_layer:
                    ci.visible = True
        
        self.redraw_canvas(0, "none")
        
        # Ждем перед следующим морганием
        blink_freq = float(group.get("blink_freq", 0.0))
        if blink_freq > 0.1:
            self.after(int(blink_freq * 1000), self._blink_preview_loop)
            
    def update_blink_freq_from_entry(self, event=None):
        """Update blink frequency from text entry"""
        try:
            value = float(self.blink_freq_entry.get())
            if 0 <= value <= 10:
                self.blink_freq.set(value)
        except ValueError:
            pass

    # ------------- export zip -------------
    def export_zip(self):
        try:
            from utils import export_model_zip
        except Exception as e:
            messagebox.showerror("Export error", f"Не удалось импортировать утилиту экспорта: {e}")
            return
        if not self.model_dir:
            messagebox.showwarning("No model", "Сначала сохраните или импортируйте изображения")
            return
        try:
            zip_path = export_model_zip(self.model, self.model_dir)
            messagebox.showinfo("Exported", f"Модель экспортирована: {zip_path}")
        except Exception as e:
            import traceback
            tb = traceback.format_exc()
            with open("export_zip_error.log", "w", encoding="utf-8") as f:
                f.write(tb)
            messagebox.showerror("Export error", f"Ошибка при экспорте: {e}. Смотри export_zip_error.log")

    # ------------- preview / autosave loop -------------
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
                                "file": ci.layer.get("file"),
                                "x": int(ci.x),
                                "y": int(ci.y),
                                "visible": bool(ci.visible),
                                "is_gif": ci.is_gif,
                                "scale": float(ci.scale),
                                "rotation": int(ci.rotation),
                                "group": ci.layer.get("group", None)
                            })
                        with open(os.path.join(self.model_dir, "model.json"), "w", encoding="utf-8") as f:
                            json.dump(temp, f, indent=2, ensure_ascii=False)
                except Exception as e:
                    print("Autosave error", e)
                self.last_autosave = now
            
            # Update audio level based on test mode
            mode = self.test_mode_var.get()
            if mode == "microphone":
                level = self.audio_level
            elif mode == "simulate":
                t = time.time()
                level = (math.sin(t * 2) + 1) / 2
                # Apply sensitivity for simulation
                level = level * self.mic_sensitivity
                self.level_bar["value"] = level * 100
            else:  # none
                level = 0.0
                
            # Update preview
            self.redraw_canvas(level, mode)
        except Exception as e:
            print("Preview loop error", e)
        finally:
            if self.winfo_exists():
                self.after(int(1000 / self.preview_fps), self._preview_loop)