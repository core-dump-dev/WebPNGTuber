
SimplePNGTuber - Python (minimal working prototype)
--------------------------------------------------
This is a minimal working prototype inspired by SimplePNGTuber.
Features included:
  - Tkinter main window with 6 model slots (2x3)
  - Simple Model Editor (import sample layers, group, set basic effects)
  - Audio input support via sounddevice (if unavailable, use simulated slider)
  - Renderer compositing frames at ~30 FPS using Pillow
  - Flask webserver streaming MJPEG at http://localhost:6969/stream (for OBS capture)
  - Import/export model as ZIP (images + model.json)
  - Autosave every 5 seconds while editing

How to run:
  1. Create virtualenv (recommended) and install requirements:
     pip install -r requirements.txt
  2. Run main:
     python main.py
  3. In the app, open Model Editor, load layers, toggle effects, start server.
  4. In OBS, add Browser source pointing to: http://localhost:6969/stream
