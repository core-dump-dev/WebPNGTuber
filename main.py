# main.py
import threading
import tkinter as tk
from tkinter import ttk, messagebox
from editor import ModelEditor
from renderer import Renderer
from webserver import WebServer
from audio import AudioProcessor
import os
import json
from PIL import Image, ImageTk
import sounddevice as sd  # оставить, чтобы get_audio_devices работал как раньше

MODELS_DIR = os.path.join(os.path.dirname(__file__), "models")
os.makedirs(MODELS_DIR, exist_ok=True)
SETTINGS_FILE = "settings.json"

class App:
    def __init__(self, root):
        self.root = root
        root.title("SimplePNGTuber (Python Prototype)")
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

        # Загрузка настроек
        self.settings = self.load_settings()

        # Renderer and Audio
        self.renderer = Renderer(width=700, height=700, fps=60)
        self.audio = AudioProcessor(callback=self.on_audio_level,
                                   device=self.settings.get('mic_device'))
        self.audio.noise_gate_threshold = 0.01  # default value
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
            'blink': True
        })
        self.renderer.set_effects(self.effects)

        # UI layout
        frame = ttk.Frame(root, padding=8)
        frame.pack(fill="both", expand=True)

        # Model slots 3x2 (6 buttons)
        slots_frame = ttk.LabelFrame(frame, text="Model slots (2×3)")
        slots_frame.grid(row=0, column=0, sticky="nsew", padx=4, pady=4)
        self.model_slots = []
        self.slot_previews = [None] * 6

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

                btn = ttk.Button(slots_frame, text=f"Slot {idx+1}\n(empty)", width=20,
                                 image=photo, compound="top",
                                 command=lambda i=idx: self.load_slot(i))
                btn.grid(row=r, column=c, padx=6, pady=6)
                self.model_slots.append(btn)

        # Controls
        ctrl_frame = ttk.LabelFrame(frame, text="Controls")
        ctrl_frame.grid(row=0, column=1, sticky="nsew", padx=4, pady=4)

        self.editor_btn = ttk.Button(ctrl_frame, text="Open Model Editor", command=self.open_editor)
        self.editor_btn.pack(fill="x", padx=8, pady=6)

        self.server_btn = ttk.Button(ctrl_frame, text="Start Web Server", command=self.toggle_server)
        self.server_btn.pack(fill="x", padx=8, pady=6)

        # Mic controls with threshold visualization
        mic_frame = ttk.LabelFrame(ctrl_frame, text="Microphone")
        mic_frame.pack(fill="x", padx=8, pady=6)

        # Device selection
        ttk.Label(mic_frame, text="Input Device:").pack(anchor='w')
        self.device_var = tk.StringVar(value=self.settings.get('mic_device', 'Default'))
        self.device_combo = ttk.Combobox(mic_frame, textvariable=self.device_var)
        self.device_combo.pack(fill='x')

        # Populate devices
        self.devices = self.get_audio_devices()
        self.device_combo['values'] = self.devices
        self.device_combo.bind('<<ComboboxSelected>>', self.on_device_change)

        ttk.Label(mic_frame, text="Input Level:").pack(anchor="w")
        self.vol_label = ttk.Label(mic_frame, text="Level: 0.00")
        self.vol_label.pack(anchor="w")

        self.sensitivity = tk.DoubleVar(value=self.settings.get('sensitivity', 1.0))
        ttk.Label(mic_frame, text="Sensitivity").pack(anchor="w")
        ttk.Scale(mic_frame, from_=0.1, to=5.0, variable=self.sensitivity, orient="horizontal").pack(fill="x")

        # Noise gate - заменено на чекбокс
        self.noise_gate_enabled = tk.BooleanVar(value=self.settings.get('noise_gate_enabled', True))
        ttk.Checkbutton(mic_frame, text="Noise Gate", variable=self.noise_gate_enabled,
                       command=self.toggle_noise_gate).pack(anchor="w")

        # Custom level indicator with thresholds
        ttk.Label(mic_frame, text="Level Indicator:").pack(anchor="w", pady=(5,0))
        self.level_canvas = tk.Canvas(mic_frame, width=200, height=40, bg="#f0f0f0")
        self.level_canvas.pack(fill="x", pady=5)

        # Create threshold lines (initial coords will be updated)
        self.threshold_lines = {
            'silent': self.level_canvas.create_line(0, 0, 0, 40, dash=(2,2), width=1),
            'whisper': self.level_canvas.create_line(0, 0, 0, 40, dash=(2,2), width=1),
            'normal': self.level_canvas.create_line(0, 0, 0, 40, dash=(2,2), width=1),
            'shout': self.level_canvas.create_line(0, 0, 0, 40, dash=(2,2), width=1)
        }

        # Level indicator — теперь с fill (видимый бар). Добавлен тег для удобства.
        self.level_indicator = self.level_canvas.create_rectangle(0, 0, 0, 40, outline="", fill="#4CAF50", tags="level_bar")

        # Bind canvas resize to update thresholds
        self.level_canvas.bind("<Configure>", self.on_canvas_resize)

        # Voice thresholds
        thresh_frame = ttk.LabelFrame(ctrl_frame, text="Voice Thresholds")
        thresh_frame.pack(fill="x", padx=8, pady=6)

        ttk.Label(thresh_frame, text="Silent:").grid(row=0, column=0, sticky="w", padx=2)
        self.silent_thresh = tk.DoubleVar(value=self.thresholds['silent'])
        ttk.Entry(thresh_frame, textvariable=self.silent_thresh, width=8).grid(row=0, column=1, padx=2)

        ttk.Label(thresh_frame, text="Whisper:").grid(row=0, column=2, sticky="w", padx=2)
        self.whisper_thresh = tk.DoubleVar(value=self.thresholds['whisper'])
        ttk.Entry(thresh_frame, textvariable=self.whisper_thresh, width=8).grid(row=0, column=3, padx=2)

        ttk.Label(thresh_frame, text="Normal:").grid(row=1, column=0, sticky="w", padx=2)
        self.normal_thresh = tk.DoubleVar(value=self.thresholds['normal'])
        ttk.Entry(thresh_frame, textvariable=self.normal_thresh, width=8).grid(row=1, column=1, padx=2)

        ttk.Label(thresh_frame, text="Shout:").grid(row=1, column=2, sticky="w", padx=2)
        self.shout_thresh = tk.DoubleVar(value=self.thresholds['shout'])
        ttk.Entry(thresh_frame, textvariable=self.shout_thresh, width=8).grid(row=1, column=3, padx=2)

        ttk.Button(thresh_frame, text="Apply", command=self.update_thresholds).grid(
            row=2, column=0, columnspan=4, pady=4, sticky="ew")

        help_label = ttk.Label(
            thresh_frame,
            text="Values: 0.0-1.0 (0=min, 1=max volume)",
            font=("Arial", 8)
        )
        help_label.grid(row=3, column=0, columnspan=4, pady=(0,4))

        # Voice states activation
        states_frame = ttk.LabelFrame(ctrl_frame, text="Active Voice States")
        states_frame.pack(fill="x", padx=8, pady=6)

        self.state_vars = {
            'silent': tk.BooleanVar(value=self.settings.get('active_states', {}).get('silent', True)),
            'whisper': tk.BooleanVar(value=self.settings.get('active_states', {}).get('whisper', True)),
            'normal': tk.BooleanVar(value=self.settings.get('active_states', {}).get('normal', True)),
            'shout': tk.BooleanVar(value=self.settings.get('active_states', {}).get('shout', True))
        }

        ttk.Checkbutton(states_frame, text="Silent", variable=self.state_vars['silent']).grid(row=0, column=0, sticky="w", padx=5)
        ttk.Checkbutton(states_frame, text="Whisper", variable=self.state_vars['whisper']).grid(row=0, column=1, sticky="w", padx=5)
        ttk.Checkbutton(states_frame, text="Normal", variable=self.state_vars['normal']).grid(row=1, column=0, sticky="w", padx=5)
        ttk.Checkbutton(states_frame, text="Shout", variable=self.state_vars['shout']).grid(row=1, column=1, sticky="w", padx=5)

        ttk.Button(states_frame, text="Apply States", command=self.update_active_states).grid(row=2, column=0, columnspan=2, pady=5)

        # Effects toggles
        effects_frame = ttk.LabelFrame(ctrl_frame, text="Global Effects")
        effects_frame.pack(fill="x", padx=8, pady=6)
        self.shake = tk.BooleanVar(value=self.effects.get('shake', False))
        ttk.Checkbutton(effects_frame, text="Shake", variable=self.shake).pack(anchor="w")
        self.bounce = tk.BooleanVar(value=self.effects.get('bounce', False))
        ttk.Checkbutton(effects_frame, text="Bounce", variable=self.bounce).pack(anchor="w")
        self.pulse = tk.BooleanVar(value=self.effects.get('pulse', False))
        ttk.Checkbutton(effects_frame, text="Pulse", variable=self.pulse).pack(anchor="w")
        self.blink = tk.BooleanVar(value=self.effects.get('blink', True))
        ttk.Checkbutton(effects_frame, text="Blink (eyes)", variable=self.blink, command=lambda: self.renderer.set_effects(self.get_effects())).pack(anchor="w")
        self.random_effect = tk.BooleanVar(value=self.effects.get('random_effect', False))
        ttk.Checkbutton(effects_frame, text="Random State Switching", variable=self.random_effect, command=lambda: self.renderer.set_effects(self.get_effects())).pack(anchor="w")

        # Save settings button
        ttk.Button(ctrl_frame, text="Save Settings", command=self.save_settings).pack(fill="x", padx=8, pady=10)

        # Start audio processing
        self.audio.start()
        self.toggle_noise_gate()  # Apply initial noise gate state

        # Start renderer
        self.renderer.start()
        self.renderer.set_thresholds(self.thresholds)
        self.renderer.set_noise_gate(0.01 if self.noise_gate_enabled.get() else 0.0)

        # Apply initial states
        self.update_active_states()

        # Update thresholds visuals
        # call once now - canvas might not have width yet; on_canvas_resize will update later
        self.update_threshold_visuals()

        # finally — обновляем слоты (вызов после создания self.model_slots)
        self.refresh_slot_buttons()

    def get_audio_devices(self):
        """Get list of available audio input devices"""
        try:
            devices = sd.query_devices()
            input_devices = ["Default"]  # Всегда добавляем Default
            
            for i, dev in enumerate(devices):
                # Фильтруем только реальные микрофоны (игнорируем виртуальные)
                if dev.get('max_input_channels', 0) > 0:
                    name = dev.get('name', '')
                    # Исключаем виртуальные устройства по ключевым словам
                    if "CABLE" in name or "VB-Audio" in name or "Voicemee" in name or "virtual" in name.lower():
                        continue
                    input_devices.append(name)
            
            return input_devices
        except Exception:
            return ["Default"]

    def on_device_change(self, event):
        """Handle audio device change"""
        device_name = self.device_var.get()
        try:
            self.audio.stop()
        except:
            pass
        self.audio = AudioProcessor(callback=self.on_audio_level, device=device_name)
        self.toggle_noise_gate()  # Reapply noise gate setting
        self.audio.start()

    def toggle_noise_gate(self):
        """Toggle noise gate on/off"""
        enabled = self.noise_gate_enabled.get()
        threshold = 0.01 if enabled else 0.0
        self.audio.noise_gate_threshold = threshold
        self.renderer.set_noise_gate(threshold)

    def get_effects(self):
        """Get current effects settings"""
        effects = {
            'shake': self.shake.get(),
            'bounce': self.bounce.get(),
            'pulse': self.pulse.get(),
            'blink': self.blink.get(),
            'random_effect': self.random_effect.get()
        }
        self.renderer.set_effects(effects)
        return effects

    def load_settings(self):
        """Load settings from file"""
        if os.path.exists(SETTINGS_FILE):
            try:
                with open(SETTINGS_FILE, 'r') as f:
                    return json.load(f)
            except:
                pass
        return {}

    def save_settings(self):
        """Save settings to file"""
        settings = {
            'thresholds': self.thresholds,
            'active_states': {state: var.get() for state, var in self.state_vars.items()},
            'effects': self.get_effects(),
            'sensitivity': self.sensitivity.get(),
            'noise_gate_enabled': self.noise_gate_enabled.get(),
            'mic_device': self.device_var.get()
        }
        try:
            with open(SETTINGS_FILE, 'w') as f:
                json.dump(settings, f, indent=2)
            messagebox.showinfo("Settings Saved", "Settings have been saved successfully.")
        except Exception as e:
            messagebox.showerror("Save error", f"Failed to save settings: {e}")

    def refresh_slot_buttons(self):
        """Refresh slot buttons with current model information"""
        # ensure model_slots present
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
                    model_name = model_data.get('name', f"Slot {idx+1}")
                    btn.config(text=f"Slot {idx+1}\n{model_name}")
                except:
                    btn.config(text=f"Slot {idx+1}\n(corrupted)")
            else:
                btn.config(text=f"Slot {idx+1}\n(empty)")

            # Update preview image
            if os.path.exists(preview_path):
                try:
                    img = Image.open(preview_path)
                    photo = ImageTk.PhotoImage(img)
                    self.slot_previews[idx] = photo
                    btn.config(image=photo)
                except:
                    btn.config(image='')
            else:
                btn.config(image='')

    def update_active_states(self):
        """Update active states in renderer"""
        active_states = {}
        for state, var in self.state_vars.items():
            active_states[state] = var.get()
        self.renderer.set_active_states(active_states)

    def update_thresholds(self):
        """Update voice level thresholds"""
        self.thresholds = {
            'silent': self.silent_thresh.get(),
            'whisper': self.whisper_thresh.get(),
            'normal': self.normal_thresh.get(),
            'shout': self.shout_thresh.get()
        }
        self.renderer.set_thresholds(self.thresholds)
        self.update_threshold_visuals()

    def update_threshold_visuals(self):
        """Update the positions of threshold lines on the canvas"""
        canvas_width = self.level_canvas.winfo_width()
        if canvas_width < 10:  # Skip if canvas not visible yet
            return

        # Remove old labels
        self.level_canvas.delete("threshold_label")

        # Update line positions and add labels
        for key in self.thresholds:
            try:
                val = float(self.thresholds[key])
            except Exception:
                val = 0.0
            pos = min(1.0, max(0.0, val)) * canvas_width
            self.level_canvas.coords(self.threshold_lines[key], pos, 0, pos, 40)
            # Add text label above the line
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
        """Update the level indicator bar with dynamic color based on thresholds"""
        canvas_width = self.level_canvas.winfo_width()
        if canvas_width < 10:
            return
        # clamp level to 0..1
        level_clamped = min(1.0, max(0.0, float(level)))
        indicator_width = level_clamped * canvas_width
        try:
            self.level_canvas.coords(self.level_indicator, 0, 0, indicator_width, 40)
        except Exception:
            pass

        # choose color according to thresholds
        try:
            t = self.thresholds
            s = float(t.get('silent', 0.05))
            w = float(t.get('whisper', 0.25))
            n = float(t.get('normal', 0.6))
            # color decisions: silent -> gray, whisper -> blue, normal -> green, shout -> red
            if level_clamped <= s:
                color = "#888888"
            elif level_clamped <= w:
                color = "#2196F3"
            elif level_clamped <= n:
                color = "#4CAF50"
            else:
                color = "#f44336"
            self.level_canvas.itemconfig(self.level_indicator, fill=color)
        except Exception:
            pass

    def on_canvas_resize(self, event=None):
        """Handle canvas resize event"""
        self.update_threshold_visuals()
        self.update_level_indicator(self.audio_level_scaled if hasattr(self, 'audio_level_scaled') else 0)

    def load_slot(self, idx):
        """Load model from slot"""
        slot_dir = os.path.join(MODELS_DIR, f"slot{idx+1}")
        json_path = os.path.join(slot_dir, "model.json")

        if not os.path.exists(json_path):
            answer = messagebox.askyesno("No model",
                f"No model found in slot {idx+1}. Create a new one?")
            if not answer:
                return

            # Создаем базовую структуру модели
            self.renderer.model = {"name": f"Slot {idx+1}", "layers": [], "groups": []}
            self.renderer.model_dir = slot_dir
            os.makedirs(slot_dir, exist_ok=True)

            # Сохраняем пустую модель
            with open(json_path, "w", encoding="utf-8") as f:
                json.dump(self.renderer.model, f, indent=2, ensure_ascii=False)
        else:
            with open(json_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            self.renderer.load_model(data, slot_dir)

        model_name = self.renderer.model.get('name','model')
        self.model_slots[idx].config(text=f"Slot {idx+1}\n{model_name}")

        # Обновляем превью
        preview_path = os.path.join(slot_dir, "preview.png")
        if os.path.exists(preview_path):
            try:
                img = Image.open(preview_path)
                photo = ImageTk.PhotoImage(img)
                self.slot_previews[idx] = photo
                self.model_slots[idx].config(image=photo)
            except:
                pass

            messagebox.showinfo("Loaded", f"Loaded model from slot {idx+1}")
        else:
            messagebox.showwarning("No model", f"No preview found in {slot_dir}")

    def open_editor(self):
        """Open model editor window"""
        try:
            # Сохраняем ссылку на главное окно
            main_window = self.root
            
            # Отключаем главное окно
            main_window.attributes('-disabled', True)
            
            # Передаем текущие настройки микрофона в редактор
            editor = ModelEditor(
                main_window, 
                on_save=self.on_model_saved,
                device=self.device_var.get(),
                noise_gate_enabled=self.noise_gate_enabled.get(),
                sensitivity=self.sensitivity.get(),
                thresholds=self.thresholds
            )
            
            # Настраиваем поведение при закрытии редактора
            editor.protocol("WM_DELETE_WINDOW", lambda: self.on_editor_close(editor))
            
            # Ждем закрытия редактора
            editor.wait_window(editor)
            
        except Exception as e:
            import traceback
            tb = traceback.format_exc()
            with open("error.log", "w", encoding="utf-8") as f:
                f.write(tb)
            messagebox.showerror("Editor error", f"Failed to open editor: {e}. See error.log")
            main_window.attributes('-disabled', False)

    def on_editor_close(self, editor):
        """Handle editor window closing"""
        try:
            # Останавливаем аудиопроцессор редактора
            editor.audio_processor.stop()
        except Exception:
            pass
        
        # Включаем главное окно
        self.root.attributes('-disabled', False)
        self.root.focus_set()
        
        # Обновляем UI главного окна
        self.refresh_slot_buttons()
        
        # Перезапускаем аудио главного окна
        try:
            self.audio.stop()
            self.audio = AudioProcessor(
                callback=self.on_audio_level,
                device=self.device_var.get()
            )
            self.audio.noise_gate_threshold = 0.01 if self.noise_gate_enabled.get() else 0.0
            self.audio.start()
        except Exception as e:
            print("Audio restart error:", e)
        
        # Закрываем окно редактора
        editor.destroy()

    def on_model_saved(self, model_data, model_dir):
        """Callback when model is saved from editor"""
        self.renderer.load_model(model_data, model_dir)

        # Обновляем веб-сервер без перезапуска
        if self.webserver:
            self.webserver.renderer = self.renderer

    def toggle_server(self):
        """Toggle web server on/off"""
        if self.webserver and getattr(self.webserver, "is_running", False):
            self.webserver.stop()
            self.server_btn.config(text="Start Web Server")
        else:
            self.webserver = WebServer(self.renderer)
            self.webserver.start()
            self.server_btn.config(text="Stop Web Server")

    def on_audio_level(self, level):
        """Handle audio level updates"""
        # level expected 0..1 typically; apply sensitivity
        try:
            self.audio_level_scaled = level * self.sensitivity.get()
        except Exception:
            self.audio_level_scaled = level
        try:
            self.vol_label.config(text=f"Level: {self.audio_level_scaled:.2f}")
        except:
            pass
        self.update_level_indicator(self.audio_level_scaled)
        # update visuals (threshold lines don't need update each frame but harmless)
        # self.update_threshold_visuals()
        self.renderer.set_audio_level(self.audio_level_scaled)

    def on_close(self):
        """Handle application close"""
        try:
            self.audio.stop()
        except:
            pass
        try:
            self.renderer.stop()
        except:
            pass
        if self.webserver:
            try:
                self.webserver.stop()
            except:
                pass
        self.save_settings()  # Save settings on close
        self.root.destroy()

if __name__ == "__main__":
    root = tk.Tk()
    app = App(root)
    root.mainloop()