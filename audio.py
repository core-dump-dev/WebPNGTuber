import threading, time
import numpy as np
import sounddevice as sd

class AudioProcessor:
    def __init__(self, callback=None, device=None):
        self.callback = callback
        self.running = False
        self._level = 0.0
        self._thread = None
        self.device = device
        self.noise_gate_threshold = 0.01
        self.device_index = None  # Для хранения индекса устройства

        # Получаем индекс устройства по имени
        if device and device != "Default":
            devices = sd.query_devices()
            for i, dev in enumerate(devices):
                if dev['name'] == device and dev['max_input_channels'] > 0:
                    self.device_index = i
                    break

    def start(self):
        """Start audio processing"""
        if self.running:
            return
        self.running = True
        self._thread = threading.Thread(target=self._capture_loop, daemon=True)
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
            device_params = {}
            if self.device_index is not None:
                device_params['device'] = self.device_index
                
            with sd.InputStream(
                channels=1, 
                callback=callback, 
                samplerate=44100, 
                blocksize=512,  # Reduced block size for lower latency
                **device_params
            ):
                while self.running:
                    try:
                        data = q.get(timeout=0.5)
                    except queue.Empty:
                        continue
                    rms = np.sqrt(np.mean(data**2))
                    level = min(1.0, rms*10)
                    
                    # Apply noise gate
                    if level < self.noise_gate_threshold:
                        level = 0.0
                    
                    self._level = level
                    if self.callback:
                        try:
                            self.callback(level)
                        except:
                            pass
        except Exception as e:
            print("Audio capture error:", e)
            # Fallback на симуляцию при ошибке
            self._simulate_loop()