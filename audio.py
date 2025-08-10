import threading, time
import numpy as np

class AudioProcessor:
    def __init__(self, callback=None):
        self.callback = callback
        self.running = False
        self._level = 0.0
        self._thread = None
        try:
            import sounddevice as sd
            self.sd = sd
            self.use_sounddevice = True
        except Exception:
            self.sd = None
            self.use_sounddevice = False

    def start(self):
        """Start audio processing"""
        if self.running:
            return
        self.running = True
        if self.use_sounddevice:
            self._thread = threading.Thread(target=self._capture_loop, daemon=True)
        else:
            self._thread = threading.Thread(target=self._simulate_loop, daemon=True)
        self._thread.start()

    def stop(self):
        """Stop audio processing"""
        self.running = False
        if self._thread:
            self._thread.join(timeout=1.0)
        self._thread = None

    def _simulate_loop(self):
        """Simulated audio loop (for testing)"""
        t = 0.0
        while self.running:
            t += 0.1
            level = (np.sin(t)+1)/2
            self._level = level
            if self.callback:
                try:
                    self.callback(level)
                except:
                    pass
            time.sleep(0.05)

    def _capture_loop(self):
        """Real audio capture loop"""
        import queue
        q = queue.Queue()
        
        def callback(indata, frames, time_info, status):
            """Audio callback function"""
            if not self.running:
                return
            q.put(indata.copy())
        
        try:
            with self.sd.InputStream(
                channels=1, 
                callback=callback, 
                samplerate=44100, 
                blocksize=1024
            ):
                while self.running:
                    try:
                        data = q.get(timeout=0.5)
                    except queue.Empty:
                        continue
                    rms = np.sqrt(np.mean(data**2))
                    level = min(1.0, rms*10)
                    self._level = level
                    if self.callback:
                        try:
                            self.callback(level)
                        except:
                            pass
        except Exception as e:
            print("Audio capture error:", e)
            self.use_sounddevice = False
            self._simulate_loop()