import os
import json
import time
import tkinter as tk
from tkinter import ttk, filedialog, simpledialog, messagebox
from PIL import Image, ImageTk
import shutil
import math
from audio import AudioProcessor

MODELS_DIR = os.path.join(os.path.dirname(__file__), "models")
os.makedirs(MODELS_DIR, exist_ok=True)

class CanvasItem:
    def __init__(self, layer, image):
        self.layer = layer
        self.image = image
        self.tkimage = ImageTk.PhotoImage(self.image)
        self.x = int(layer.get("x", 0))
        self.y = int(layer.get("y", 0))
        self.visible = bool(layer.get("visible", True))

class ModelEditor(tk.Toplevel):
    def __init__(self, master, on_save=None):
        super().__init__(master)
        self.title("Model Editor")
        self.geometry("1200x750")
        self.on_save = on_save
        self.protocol("WM_DELETE_WINDOW", self.on_close)

        # Model storage
        self.model = {"name": "Untitled", "layers": [], "groups": []}
        self.model_dir = None
        self.items = []
        self.imported_files = []
        self.drag_data = {"item": None, "x": 0, "y": 0}
        self.selected_group = None
        self.preview_fps = 24
        self.last_autosave = time.time()
        self.autosave_interval = 5.0
        self.audio_level = 0.0

        # ---- UI layout ----
        left = ttk.Frame(self, width=260)
        left.pack(side="left", fill="y", padx=6, pady=6)

        ttk.Button(left, text="New Model", command=self.new_model).pack(fill="x", pady=2)
        ttk.Button(left, text="Load Model", command=self.load_model).pack(fill="x", pady=2)
        ttk.Button(left, text="Save Model", command=self.save_model).pack(fill="x", pady=2)
        ttk.Button(left, text="Import PNG(s)", command=self.import_images).pack(fill="x", pady=2)
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
        self.level_bar = ttk.Progressbar(test_frame, length=200, mode="determinate")
        self.level_bar.pack(fill="x", pady=5)
        
        # Start audio processor
        self.audio_processor = AudioProcessor(callback=self.on_audio_level)

        ttk.Label(left, text="Imported PNGs:").pack(anchor="w", pady=(8, 0))
        self.import_list_frame = ttk.Frame(left)
        self.import_list_frame.pack(fill="both", expand=True)

        self.import_canvas = tk.Canvas(self.import_list_frame, width=240, height=320)
        self.import_vscroll = ttk.Scrollbar(self.import_list_frame, orient="vertical", command=self.import_canvas.yview)
        self.import_canvas.configure(yscrollcommand=self.import_vscroll.set)
        self.import_vscroll.pack(side="right", fill="y")
        self.import_canvas.pack(side="left", fill="both", expand=True)
        self.import_inner = ttk.Frame(self.import_canvas)
        self.import_canvas.create_window((0, 0), window=self.import_inner, anchor="nw")
        self.import_inner.bind("<Configure>", lambda e: self.import_canvas.configure(scrollregion=self.import_canvas.bbox("all")))

        # Center: canvas preview
        center = ttk.Frame(self)
        center.pack(side="left", fill="both", expand=True, padx=6, pady=6)
        preview_frame = ttk.LabelFrame(center, text="Canvas Preview")
        preview_frame.pack(fill="both", expand=True)
        self.canvas_w = 700
        self.canvas_h = 700
        self.canvas = tk.Canvas(preview_frame, width=self.canvas_w, height=self.canvas_h, bg="#222")
        self.canvas.pack(expand=True)
        self.canvas.bind("<ButtonPress-1>", self.on_canvas_mouse_down)
        self.canvas.bind("<B1-Motion>", self.on_canvas_mouse_move)
        self.canvas.bind("<ButtonRelease-1>", self.on_canvas_mouse_up)

        # Right: items & properties
        right = ttk.Frame(self, width=340)
        right.pack(side="right", fill="y", padx=6, pady=6)

        items_frame = ttk.LabelFrame(right, text="Canvas Items (top → bottom)")
        items_frame.pack(fill="both", expand=True)
        self.items_listbox = tk.Listbox(items_frame, selectmode="extended", height=20)
        self.items_listbox.pack(fill="both", expand=True)
        self.items_listbox.bind("<<ListboxSelect>>", self.on_list_select)

        btns = ttk.Frame(items_frame)
        btns.pack(fill="x")
        ttk.Button(btns, text="Bring Forward", command=self.bring_forward).pack(side="left", padx=2, pady=4)
        ttk.Button(btns, text="Send Backward", command=self.send_backward).pack(side="left", padx=2, pady=4)
        ttk.Button(btns, text="Group Selected", command=self.group_selected).pack(side="left", padx=2, pady=4)
        ttk.Button(btns, text="Ungroup Selected", command=self.ungroup_selected).pack(side="left", padx=2, pady=4)

        props = ttk.LabelFrame(right, text="Selected Properties")
        props.pack(fill="x", pady=6)
        ttk.Label(props, text="Name").pack(anchor="w")
        self.name_entry = ttk.Entry(props)
        self.name_entry.pack(fill="x")
        ttk.Label(props, text="X").pack(anchor="w")
        self.x_entry = ttk.Entry(props)
        self.x_entry.pack(fill="x")
        ttk.Label(props, text="Y").pack(anchor="w")
        self.y_entry = ttk.Entry(props)
        self.y_entry.pack(fill="x")
        self.visible_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(props, text="Visible", variable=self.visible_var).pack(anchor="w")
        self.blink_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(props, text="Blink", variable=self.blink_var).pack(anchor="w")
        self.speech_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(props, text="React to speech", variable=self.speech_var).pack(anchor="w")
        ttk.Button(props, text="Apply to Selection", command=self.apply_props).pack(fill="x", pady=4)

        # Group logic editor
        self.group_logic_frame = ttk.LabelFrame(right, text="Group Logic (select a group)")
        self.group_logic_frame.pack(fill="x", pady=6)
        ttk.Label(self.group_logic_frame, text="Selected Group:").pack(anchor="w")
        self.group_label = ttk.Label(self.group_logic_frame, text="(no group)")
        self.group_label.pack(anchor="w")

        ttk.Label(self.group_logic_frame, text="Map state → child layer").pack(anchor="w", pady=(6, 0))
        self.state_vars = {
            "silent": tk.StringVar(value=""),
            "whisper": tk.StringVar(value=""),
            "normal": tk.StringVar(value=""),
            "shout": tk.StringVar(value=""),
            "blink": tk.StringVar(value="")
        }
        self.state_optionmenus = {}
        for s in ("silent", "whisper", "normal", "shout", "blink"):
            frame = ttk.Frame(self.group_logic_frame)
            frame.pack(fill="x", pady=2)
            ttk.Label(frame, text=s.capitalize(), width=10).pack(side="left")
            om = ttk.OptionMenu(frame, self.state_vars[s], "")
            om.pack(side="left", fill="x", expand=True)
            self.state_optionmenus[s] = om

        ttk.Label(self.group_logic_frame, text="Blink frequency (sec, 0=off)").pack(anchor="w", pady=(6, 0))
        self.blink_freq = tk.DoubleVar(value=0.0)
        ttk.Scale(self.group_logic_frame, from_=0.0, to=10.0, variable=self.blink_freq, orient="horizontal").pack(fill="x")
        ttk.Button(self.group_logic_frame, text="Apply Group Logic", command=self.apply_group_logic).pack(fill="x", pady=6)

        # Start preview loop
        self.after(100, self._preview_loop)

    def on_close(self):
        """Handle window close event"""
        try:
            self.audio_processor.stop()
        except Exception as e:
            print("Error stopping audio processor:", e)
        self.destroy()

    def update_test_mode(self):
        """Update test mode based on selection"""
        mode = self.test_mode_var.get()
        if mode == "microphone":
            self.audio_processor.start()
        else:
            self.audio_processor.stop()
            if mode == "none":
                # Reset level for None mode
                self.audio_level = 0.0
                self.level_bar["value"] = 0

    def on_audio_level(self, level):
        """Callback for audio level updates"""
        self.audio_level = level
        if self.test_mode_var.get() == "microphone":
            self.level_bar["value"] = level * 100

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
        path = filedialog.askdirectory(title="Select model folder")
        if not path:
            return
        json_path = os.path.join(path, "model.json")
        if not os.path.exists(json_path):
            messagebox.showerror("Error", "model.json not found in selected folder")
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
                img = Image.open(fp).convert("RGBA")
                ci = CanvasItem(layer, img)
                ci.x = int(layer.get("x", 0))
                ci.y = int(layer.get("y", 0))
                ci.visible = bool(layer.get("visible", True))
                self.items.append(ci)
            except Exception as e:
                print("Load image error", e)
        self.imported_files.clear()
        for f in os.listdir(self.model_dir):
            if f.lower().endswith(".png"):
                try:
                    img = Image.open(os.path.join(self.model_dir, f)).convert("RGBA")
                    self.imported_files.append((f, img))
                except Exception:
                    pass
        self.refresh_import_list()
        self.refresh_items_list()
        self.redraw_canvas()

    def save_model(self):
        if not self.model_dir:
            name = self.model.get("name", "model")
            folder = filedialog.askdirectory(title="Choose parent folder for model (a new folder will be created)")
            if not folder:
                return
            dest = os.path.join(folder, name.replace(" ", "_"))
            os.makedirs(dest, exist_ok=True)
            self.model_dir = dest
        for idx, ci in enumerate(self.items):
            layer = ci.layer
            fname = layer.get("file")
            if fname and os.path.exists(os.path.join(self.model_dir, fname)):
                continue
            if fname:
                ci.image.save(os.path.join(self.model_dir, fname))
            else:
                fname = f"layer_{idx}.png"
                ci.image.save(os.path.join(self.model_dir, fname))
                layer["file"] = fname
        self.model["layers"] = []
        for ci in self.items:
            layer = ci.layer
            layer["x"] = int(ci.x)
            layer["y"] = int(ci.y)
            layer["visible"] = bool(ci.visible)
            self.model["layers"].append(layer)
        with open(os.path.join(self.model_dir, "model.json"), "w", encoding="utf-8") as f:
            json.dump(self.model, f, indent=2, ensure_ascii=False)
        
        # Create preview
        self.create_preview()
        
        # Offer to save to slot
        slot = simpledialog.askinteger("Save to Slot", 
                                      "Enter slot number (1-6) to save to:",
                                      parent=self, minvalue=1, maxvalue=6)
        if slot:
            self.save_to_slot(slot)
            
        messagebox.showinfo("Saved", f"Model saved to {self.model_dir}")
        if self.on_save:
            self.on_save(self.model, self.model_dir)
        self.last_autosave = time.time()
        
    def save_to_slot(self, slot_num):
        slot_dir = os.path.join(MODELS_DIR, f"slot{slot_num}")
        os.makedirs(slot_dir, exist_ok=True)
        for fname, _ in self.imported_files:
            src = os.path.join(self.model_dir, fname)
            dst = os.path.join(slot_dir, fname)
            shutil.copy2(src, dst)
        shutil.copy2(os.path.join(self.model_dir, "model.json"), 
                    os.path.join(slot_dir, "model.json"))
        preview_src = os.path.join(self.model_dir, "preview.png")
        preview_dst = os.path.join(slot_dir, "preview.png")
        if os.path.exists(preview_src):
            shutil.copy2(preview_src, preview_dst)
        messagebox.showinfo("Saved", f"Model saved to slot {slot_num}")

    def create_preview(self):
        if not self.model_dir:
            return
        base = Image.new("RGBA", (self.canvas_w, self.canvas_h), (0, 0, 0, 0))
        center_x = self.canvas_w // 2
        center_y = self.canvas_h // 2
        for ci in self.items:
            if not ci.visible:
                continue
            img = ci.image
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
        files = filedialog.askopenfilenames(title="Select PNG images", filetypes=[("PNG", "*.png"), ("All", "*.*")])
        if not files:
            return
        if not self.model_dir:
            tmp = os.path.join(MODELS_DIR, f"model_temp_{int(time.time())}")
            os.makedirs(tmp, exist_ok=True)
            self.model_dir = tmp
        for p in files:
            try:
                img = Image.open(p).convert("RGBA")
                base = os.path.basename(p)
                dest = os.path.join(self.model_dir, base)
                if os.path.abspath(p) != os.path.abspath(dest):
                    img.save(dest)
                self.imported_files.append((base, img))
                layer = {"name": os.path.splitext(base)[0], "file": base, "blink": False, "speech": False, "visible": True, "x": 0, "y": 0, "group": None}
                self.model.setdefault("layers", []).append(layer)
            except Exception as e:
                print("Import error", e)
        self.refresh_import_list()
        self.last_autosave = time.time()

    # ------------- UI refresh helpers -------------
    def refresh_import_list(self):
        for w in self.import_inner.winfo_children():
            w.destroy()
        for i, (fname, img) in enumerate(self.imported_files):
            row = ttk.Frame(self.import_inner)
            row.pack(fill="x", padx=2, pady=2)
            ttk.Label(row, text=fname, width=18).pack(side="left")
            ttk.Button(row, text="+", width=2, command=lambda f=fname: self.add_to_canvas(f)).pack(side="left", padx=2)
            ttk.Button(row, text="-", width=2, command=lambda f=fname: self.remove_from_canvas_by_file(f)).pack(side="left", padx=2)

    def refresh_items_list(self):
        self.items_listbox.delete(0, "end")
        groups = self.model.get("groups", [])
        for g in groups:
            name = g.get("name", "(group)")
            self.items_listbox.insert("end", f"[Group] {name}")
        for i, ci in enumerate(reversed(self.items)):
            layer = ci.layer
            name = layer.get("name", f"layer{i}")
            flags = []
            if layer.get("blink"):
                flags.append("blink")
            if layer.get("speech"):
                flags.append("speech")
            grp = layer.get("group")
            label = f"{name} ({','.join(flags)})" if not grp else f"{name} @ {grp}"
            self.items_listbox.insert("end", label)

    def redraw_canvas(self):
        base = Image.new("RGBA", (self.canvas_w, self.canvas_h), (0, 0, 0, 0))
        center_x = self.canvas_w // 2
        center_y = self.canvas_h // 2
        for ci in self.items:
            if not ci.visible:
                continue
            img = ci.image
            px = center_x - img.size[0] // 2 + int(ci.x)
            py = center_y - img.size[1] // 2 + int(ci.y)
            try:
                base.alpha_composite(img, (px, py))
            except Exception:
                pass
        self.base_tk = ImageTk.PhotoImage(base)
        self.canvas.delete("all")
        self.canvas.create_image(0, 0, anchor="nw", image=self.base_tk)
        center_x = self.canvas_w // 2
        center_y = self.canvas_h // 2
        if self.selected_group:
            for ci in self.items:
                if ci.layer.get("group") == self.selected_group:
                    img = ci.image
                    px = center_x - img.size[0] // 2 + int(ci.x)
                    py = center_y - img.size[1] // 2 + int(ci.y)
                    self.canvas.create_rectangle(px, py, px + img.size[0], py + img.size[1], outline="orange", width=2)
        else:
            for ci in self.items:
                if ci.layer.get("_selected"):
                    img = ci.image
                    px = center_x - img.size[0] // 2 + int(ci.x)
                    py = center_y - img.size[1] // 2 + int(ci.y)
                    self.canvas.create_rectangle(px, py, px + img.size[0], py + img.size[1], outline="cyan", width=2)

    # ------------- canvas & items operations -------------
    def add_to_canvas(self, filename):
        for fname, img in self.imported_files:
            if fname == filename:
                layer = None
                for l in self.model.get("layers", []):
                    if l.get("file") == fname:
                        layer = l
                        break
                if not layer:
                    layer = {"name": os.path.splitext(fname)[0], "file": fname, "blink": False, "speech": False, "visible": True, "x": 0, "y": 0, "group": None}
                    self.model.setdefault("layers", []).append(layer)
                ci = CanvasItem(layer, img)
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

    # ------------- list selection handling -------------
    def on_list_select(self, event=None):
        sels = list(self.items_listbox.curselection())
        if not sels:
            for c in self.items:
                c.layer["_selected"] = False
            self.selected_group = None
            self.group_label.config(text="(no group)")
            self._clear_group_optionmenus()
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
            for s, var in self.state_vars.items():
                menu = self.state_optionmenus[s]["menu"]
                menu.delete(0, "end")
                menu.add_command(label="", command=lambda v=var: v.set(""))
                for child in children:
                    menu.add_command(label=child, command=lambda val=child, v=var: v.set(val))
                var.set(grp.get("logic", {}).get(s, ""))
            self.blink_freq.set(float(grp.get("blink_freq", 0.0)))
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
            if sel:
                first = sel[0]
                self.name_entry.delete(0, "end")
                self.name_entry.insert(0, first.layer.get("name", ""))
                self.x_entry.delete(0, "end")
                self.x_entry.insert(0, str(first.x))
                self.y_entry.delete(0, "end")
                self.y_entry.insert(0, str(first.y))
                self.visible_var.set(bool(first.visible))
                self.blink_var.set(bool(first.layer.get("blink", False)))
                self.speech_var.set(bool(first.layer.get("speech", False)))
            self.group_label.config(text="(no group)")
            self._clear_group_optionmenus()
        self.redraw_canvas()

    def _clear_group_optionmenus(self):
        for s, var in self.state_vars.items():
            var.set("")
            menu = self.state_optionmenus[s]["menu"]
            menu.delete(0, "end")
            menu.add_command(label="", command=lambda v=var: v.set(""))

    # ------------- apply properties -------------
    def apply_props(self):
        sels = [ci for ci in self.items if ci.layer.get("_selected")]
        if not sels:
            messagebox.showwarning("No selection", "Select one or more items from the right list")
            return
        name = self.name_entry.get().strip()
        try:
            x = int(self.x_entry.get().strip())
            y = int(self.y_entry.get().strip())
        except Exception:
            messagebox.showwarning("Invalid", "X and Y must be integers")
            return
        vis = self.visible_var.get()
        blink = self.blink_var.get()
        speech = self.speech_var.get()
        for ci in sels:
            if name:
                ci.layer["name"] = name
            ci.x = x
            ci.y = y
            ci.visible = vis
            ci.layer["blink"] = blink
            ci.layer["speech"] = speech
        self.refresh_items_list()
        self.redraw_canvas()
        self.last_autosave = time.time()

    # ------------- stacking controls -------------
    def bring_forward(self):
        sels = [ci for ci in self.items if ci.layer.get("_selected")]
        for ci in sels:
            idx = self.items.index(ci)
            if idx < len(self.items) - 1:
                self.items[idx], self.items[idx + 1] = self.items[idx + 1], self.items[idx]
        self.refresh_items_list()
        self.redraw_canvas()

    def send_backward(self):
        sels = [ci for ci in self.items if ci.layer.get("_selected")]
        for ci in sels:
            idx = self.items.index(ci)
            if idx > 0:
                self.items[idx], self.items[idx - 1] = self.items[idx - 1], self.items[idx]
        self.refresh_items_list()
        self.redraw_canvas()

    # ------------- grouping -------------
    def group_selected(self):
        sels = [ci for ci in self.items if ci.layer.get("_selected")]
        if len(sels) < 1:
            messagebox.showwarning("Group", "Select at least one item")
            return
        name = simpledialog.askstring("Group name", "Enter group name", parent=self)
        if not name:
            return
        existing = [g.get("name") for g in self.model.get("groups", [])]
        if name in existing:
            messagebox.showwarning("Group", "Group name already exists")
            return
        group = {"name": name, "children": [ci.layer.get("name") for ci in sels], "logic": {}, "blink_freq": 0.0}
        self.model.setdefault("groups", []).append(group)
        for ci in sels:
            ci.layer["group"] = name
        for ci in self.items:
            ci.layer["_selected"] = False
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
        sels = [ci for ci in self.items if ci.layer.get("_selected")]
        if not sels:
            return
        for ci in sels:
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
            img = ci.image
            px = center_x - img.size[0] // 2 + int(ci.x)
            py = center_y - img.size[1] // 2 + int(ci.y)
            if px <= mx <= px + img.size[0] and py <= my <= py + img.size[1]:
                found = ci
                break
        if found:
            if event.state & 0x0004:
                found.layer["_selected"] = not bool(found.layer.get("_selected"))
            else:
                for c in self.items:
                    c.layer["_selected"] = False
                found.layer["_selected"] = True
            grp = found.layer.get("group")
            if grp:
                self.selected_group = grp
                for c in self.items:
                    c.layer["_selected"] = (c.layer.get("group") == grp)
            else:
                self.selected_group = None
            self.drag_data["item"] = found
            self.drag_data["x"] = mx
            self.drag_data["y"] = my
            self.on_list_select()
        else:
            for c in self.items:
                c.layer["_selected"] = False
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
            sels = [c for c in self.items if c.layer.get("_selected")]
            if not sels:
                sels = [ci]
            for s in sels:
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
            messagebox.showwarning("No group", "Select a group first (from the right list)")
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
        messagebox.showinfo("Group logic", f"Saved logic for group {gname}")

    # ------------- export zip -------------
    def export_zip(self):
        try:
            from utils import export_model_zip
        except Exception as e:
            messagebox.showerror("Export error", f"Не удалось импортировать утилиту экспорта: {e}")
            return
        if not self.model_dir:
            messagebox.showwarning("No model", "Сначала сохраните или импортируйте изображения (чтобы создать model_dir).")
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
                                "blink": bool(ci.layer.get("blink", False)),
                                "speech": bool(ci.layer.get("speech", False)),
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
            else:  # none
                level = 0.0
                
            self.level_bar["value"] = level * 100
            
            self.redraw_canvas()
        except Exception as e:
            print("Preview loop error", e)
        finally:
            if self.winfo_exists():
                self.after(int(1000 / self.preview_fps), self._preview_loop)