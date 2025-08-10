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

MODELS_DIR = os.path.join(os.path.dirname(__file__), "models")

class App:
    def __init__(self, root):
        self.root = root
        root.title("SimplePNGTuber (Python Prototype)")
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

        # Renderer and Audio
        self.renderer = Renderer()
        self.audio = AudioProcessor(callback=self.on_audio_level)
        self.webserver = None
        self.thresholds = {
            'silent': 0.05,
            'whisper': 0.25,
            'normal': 0.6,
            'shout': 0.8
        }

        # UI layout
        frame = ttk.Frame(root, padding=8)
        frame.pack(fill="both", expand=True)

        # Model slots 2x3
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
                        pass
                
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
        ttk.Label(mic_frame, text="Input: (using sounddevice or simulated)").pack(anchor="w")
        self.vol_label = ttk.Label(mic_frame, text="Level: 0.00")
        self.vol_label.pack(anchor="w")
        self.sensitivity = tk.DoubleVar(value=1.0)
        ttk.Label(mic_frame, text="Sensitivity").pack(anchor="w")
        ttk.Scale(mic_frame, from_=0.1, to=5.0, variable=self.sensitivity, orient="horizontal").pack(fill="x")
        
        # Custom level indicator with thresholds
        ttk.Label(mic_frame, text="Level Indicator:").pack(anchor="w", pady=(5,0))
        self.level_canvas = tk.Canvas(mic_frame, width=200, height=40, bg="#f0f0f0")
        self.level_canvas.pack(fill="x", pady=5)
        
        # Create threshold lines
        self.threshold_lines = {
            'silent': self.level_canvas.create_line(0, 0, 0, 40, fill="blue", dash=(2,2), width=1),
            'whisper': self.level_canvas.create_line(0, 0, 0, 40, fill="green", dash=(2,2), width=1),
            'normal': self.level_canvas.create_line(0, 0, 0, 40, fill="orange", dash=(2,2), width=1),
            'shout': self.level_canvas.create_line(0, 0, 0, 40, fill="red", dash=(2,2), width=1)
        }
        
        # Level indicator
        self.level_indicator = self.level_canvas.create_rectangle(0, 0, 0, 40, fill="#4CAF50", outline="")
        
        # Bind canvas resize to update thresholds
        self.level_canvas.bind("<Configure>", self.on_canvas_resize)

        # Volume thresholds
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
        
        # Help text
        help_label = ttk.Label(
            thresh_frame, 
            text="Values: 0.0-1.0 (0=min, 1=max volume)", 
            font=("Arial", 8)
        )
        help_label.grid(row=3, column=0, columnspan=4, pady=(0,4))
        
        # Voice states activation
        states_frame = ttk.LabelFrame(ctrl_frame, text="Active Voice States")
        states_frame.pack(fill="x", padx=8, pady=6)
        
        # Create variables for each state
        self.state_vars = {
            'silent': tk.BooleanVar(value=True),
            'whisper': tk.BooleanVar(value=True),
            'normal': tk.BooleanVar(value=True),
            'shout': tk.BooleanVar(value=True)
        }
        
        # Arrange checkboxes in a grid
        ttk.Checkbutton(states_frame, text="Silent", variable=self.state_vars['silent']).grid(row=0, column=0, sticky="w", padx=5)
        ttk.Checkbutton(states_frame, text="Whisper", variable=self.state_vars['whisper']).grid(row=0, column=1, sticky="w", padx=5)
        ttk.Checkbutton(states_frame, text="Normal", variable=self.state_vars['normal']).grid(row=1, column=0, sticky="w", padx=5)
        ttk.Checkbutton(states_frame, text="Shout", variable=self.state_vars['shout']).grid(row=1, column=1, sticky="w", padx=5)
        
        # Apply button
        ttk.Button(states_frame, text="Apply States", command=self.update_active_states).grid(row=2, column=0, columnspan=2, pady=5)

        # Effects toggles
        effects_frame = ttk.LabelFrame(ctrl_frame, text="Global Effects")
        effects_frame.pack(fill="x", padx=8, pady=6)
        self.shake = tk.BooleanVar(value=False)
        ttk.Checkbutton(effects_frame, text="Shake", variable=self.shake).pack(anchor="w")
        self.bounce = tk.BooleanVar(value=False)
        ttk.Checkbutton(effects_frame, text="Bounce", variable=self.bounce).pack(anchor="w")
        self.pulse = tk.BooleanVar(value=False)
        ttk.Checkbutton(effects_frame, text="Pulse", variable=self.pulse).pack(anchor="w")
        self.blink = tk.BooleanVar(value=True)
        ttk.Checkbutton(effects_frame, text="Blink (eyes)", variable=self.blink).pack(anchor="w")

        # Start audio processing
        self.audio.start()

        # Start renderer
        self.renderer.start()
        self.renderer.set_thresholds(self.thresholds)
        
        # Apply initial states
        self.update_active_states()

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
        if canvas_width < 10:  # Skip if canvas not visible
            return
            
        # Remove old labels
        self.level_canvas.delete("threshold_label")
        
        # Update line positions and add labels
        for key in self.thresholds:
            pos = min(1.0, self.thresholds[key]) * canvas_width
            self.level_canvas.coords(self.threshold_lines[key], pos, 0, pos, 40)
            # Add text label above the line
            self.level_canvas.create_text(
                pos, 10, 
                text=key, 
                anchor="e" if key=="silent" else "w" if key=="shout" else "center",
                tags="threshold_label",
                font=("Arial", 8)
            )
    
    def update_level_indicator(self, level):
        """Update the level indicator bar"""
        canvas_width = self.level_canvas.winfo_width()
        if canvas_width < 10:
            return
        indicator_width = min(1.0, level) * canvas_width
        self.level_canvas.coords(self.level_indicator, 0, 0, indicator_width, 40)
    
    def on_canvas_resize(self, event=None):
        """Handle canvas resize event"""
        self.update_threshold_visuals()
        self.update_level_indicator(self.audio_level_scaled if hasattr(self, 'audio_level_scaled') else 0)

    def load_slot(self, idx):
        """Load model from slot"""
        slot_dir = os.path.join(MODELS_DIR, f"slot{idx+1}")
        json_path = os.path.join(slot_dir, "model.json")
        if os.path.exists(json_path):
            with open(json_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            self.renderer.load_model(data, slot_dir)
            model_name = data.get('name','model')
            self.model_slots[idx].config(text=f"Slot {idx+1}\n{model_name}")
            
            # Update preview if exists
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
            messagebox.showwarning("No model", f"No model found in {slot_dir}")

    def open_editor(self):
        """Open model editor window"""
        try:
            editor = ModelEditor(self.root, on_save=self.on_model_saved)
            self.root.wait_window(editor)
        except Exception as e:
            import traceback
            tb = traceback.format_exc()
            with open("error.log", "w", encoding="utf-8") as f:
                f.write(tb)
            messagebox.showerror("Editor error", f"Failed to open editor: {e}. See error.log")

    def on_model_saved(self, model_data, model_dir):
        """Callback when model is saved from editor"""
        self.renderer.load_model(model_data, model_dir)
        messagebox.showinfo("Saved", "Model saved and loaded into renderer.")

    def toggle_server(self):
        """Toggle web server on/off"""
        if self.webserver and self.webserver.is_running:
            self.webserver.stop()
            self.server_btn.config(text="Start Web Server")
        else:
            self.webserver = WebServer(self.renderer)
            self.webserver.start()
            self.server_btn.config(text="Stop Web Server")

    def on_audio_level(self, level):
        """Handle audio level updates"""
        self.audio_level_scaled = level * self.sensitivity.get()
        self.vol_label.config(text=f"Level: {self.audio_level_scaled:.2f}")
        self.update_level_indicator(self.audio_level_scaled)
        self.update_threshold_visuals()
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
            self.webserver.stop()
        self.root.destroy()

if __name__ == "__main__":
    root = tk.Tk()
    app = App(root)
    root.mainloop()