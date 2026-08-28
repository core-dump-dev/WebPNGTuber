# WebPNGTuber - Advanced PNG Tuber Editor

WebPNGTuber is an open-source application for creating and managing PNG Tuber models. It combines ease of use with advanced functionality, allowing you to build interactive avatars that react to your voice.

## ✨ Key Features

- 🎨 **Multi‑layer editor**: Build complex models from PNG and GIF images.
- 🔊 **Audio reactions**: Configure 4 reaction levels (silence, whisper, normal, shout).
- 👁️ **Automatic blinking**: Realistic eye animation with adjustable frequency.
- 🌐 **Built‑in web server**: Stream to OBS via `http://localhost:6969`.
- ⚙️ **Advanced settings**: Adjust sensitivity, noise reduction, effects.
- 💾 **Slot system**: Save up to 6 models with previews.
- 🌑 **Idle mode**: Automatically dim the avatar during inactivity.

## Model

The "Customizable Slugcat PNGTuber Model" is provided by CurioKryptic (itch.io):  
[https://curiokryptic.itch.io/slugcat-pngtuber-model](https://curiokryptic.itch.io/slugcat-pngtuber-model)

Terms: the model is free to use; use it for testing.

## 🚀 Quick Start

### For Windows users:
1. Download the latest version from the [Releases section](https://github.com/core-dump-dev/WebPNGTuber/releases).
2. Extract the archive to any folder.
3. Run `WebPNGTuber.exe`.

### For developers:
```bash
# Clone the repository
git clone https://github.com/core-dump-dev/WebPNGTuber.git

# Install dependencies
pip install -r requirements.txt

# Run the application
python main.py
```

## 🛠 Requirements
- Windows 7 or newer
- For development: Python 3.7+

<details>
<summary>📦 Full list of dependencies (requirements.txt)</summary>

```txt
Pillow==10.3.0
numpy==1.26.4
sounddevice==0.4.6
Flask==3.0.3
requests==2.31.0
```
</details>

## 🎥 OBS Integration
1. Start the web server inside the application.
2. In OBS, add a new "Browser" source.
3. Enter the URL: `http://localhost:6969`.
4. Set the size to 700x700 pixels.

## 🧩 User Guide

### Creating a model
1. Open the editor from the main window.
2. Import PNG/GIF images.
3. Arrange the layers in the desired order.
4. Adjust position, scale, and rotation of elements.
5. Group related elements (e.g., eyes).
6. Configure reactions for different volume levels.
7. Save the model to one of the 6 slots.

### Configuring effects
- **Shake**: Slight wobble on loud sounds.
- **Bounce**: Hopping animation.
- **Pulse**: Smooth size oscillation.
- **Idle mode**: Dimming when no sound is detected.

## ⚠️ Known Issues

- **Closing the main window while saving a model**  
  After saving in the editor, do not close it immediately – first check the model in the main window.

- **Limited GIF support**  
  For animations, use optimized files.

- **Sound reaction delay**  
  Make sure the correct audio device is selected in settings.

## 💡 Tips for Creating PNG Tubers

1. **Start simple**: Body + mouth + eyes.
2. **Use transparency**: PNGs with transparent backgrounds look better.
3. **Optimise GIFs**: Reduce the number of frames and colours.
4. **Experiment**: Try different reactions for different parts of the face.
5. **Save often**: Regularly save your work into different slots.