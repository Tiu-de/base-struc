import machine
import neopixel
try:
    import _thread
except ImportError:
    _thread = None

class NeoPixelController:
    def __init__(self, pin=6, num_leds=4, debug=False):
        """NeoPixel controller (mặc định im lặng, không spam log)."""
        self.num_leds = num_leds
        self.data_pin = machine.Pin(pin, machine.Pin.OUT)
        self.np = neopixel.NeoPixel(self.data_pin, self.num_leds)
        self.brightness = 100
        self.last_colors = [(0, 0, 0) for _ in range(self.num_leds)]
        self.debug = debug
        self._lock = None
        try:
            thread_safe = True
            try:
                import config as CFG
                if hasattr(CFG, "SYSTEM"):
                    thread_safe = bool(CFG.SYSTEM.get("NEOPIXEL_THREAD_SAFE", True))
            except Exception:
                pass
            if thread_safe and _thread is not None:
                self._lock = _thread.allocate_lock()
        except Exception:
            pass
        self.clear_all()

    def set_brightness(self, brightness_percent):
        try:
            brightness_percent = max(0, min(100, brightness_percent))
            if self._lock:
                with self._lock:
                    self.brightness = brightness_percent
                    for i in range(self.num_leds):
                        self._apply_led(i, self.last_colors[i])
            else:
                self.brightness = brightness_percent
                for i in range(self.num_leds):
                    self._apply_led(i, self.last_colors[i])
            return True
        except Exception as e:
            if self.debug:
                print(f"Lỗi khi thiết lập độ sáng: {e}")
            return False

    def set_led(self, led_number, r, g, b):
        try:
            if not 0 <= led_number < self.num_leds:
                return False
            r = max(0, min(255, r))
            g = max(0, min(255, g))
            b = max(0, min(255, b))
            if self._lock:
                with self._lock:
                    self.last_colors[led_number] = (r, g, b)
                    self._apply_led(led_number, (r, g, b))
            else:
                self.last_colors[led_number] = (r, g, b)
                self._apply_led(led_number, (r, g, b))
            return True
        except Exception as e:
            if self.debug:
                print(f"Lỗi khi thiết lập LED {led_number}: {e}")
            return False

    def _apply_led(self, led_number, rgb):
        r, g, b = rgb
        scale = self.brightness / 100
        self.np[led_number] = (int(r * scale), int(g * scale), int(b * scale))

    def turn_off_led(self, led_number):
        return self.set_led(led_number, 0, 0, 0)

    def clear_all(self):
        try:
            if self._lock:
                with self._lock:
                    for i in range(self.num_leds):
                        self.last_colors[i] = (0, 0, 0)
                        self._apply_led(i, (0, 0, 0))
                    self.np.write()
            else:
                for i in range(self.num_leds):
                    self.last_colors[i] = (0, 0, 0)
                    self._apply_led(i, (0, 0, 0))
                self.np.write()
            return True
        except Exception as e:
            return False

    def set_pixels(self, colors):
        try:
            if self._lock:
                with self._lock:
                    for i, c in enumerate(colors):
                        if i >= self.num_leds:
                            break
                        r, g, b = c
                        self.last_colors[i] = (max(0,min(255,int(r))), max(0,min(255,int(g))), max(0,min(255,int(b))))
                        self._apply_led(i, self.last_colors[i])
                    self.np.write()
            else:
                for i, c in enumerate(colors):
                    if i >= self.num_leds:
                        break
                    r, g, b = c
                    self.last_colors[i] = (max(0,min(255,int(r))), max(0,min(255,int(g))), max(0,min(255,int(b))))
                    self._apply_led(i, self.last_colors[i])
                self.np.write()
            return True
        except Exception as e:
            return False

    def summarize(self):
        active = [i for i, c in enumerate(self.last_colors) if c != (0, 0, 0)]
        return {'num_leds': self.num_leds, 'brightness': self.brightness,
                'last_colors': list(self.last_colors), 'active_idxs': active}

    def get_led_state(self, led_number):
        try:
            if not 0 <= led_number < self.num_leds:
                return {'status': False, 'r': None, 'g': None, 'b': None, 'brightness': None}
            r, g, b = self.last_colors[led_number]
            return {'status': True, 'r': r, 'g': g, 'b': b, 'brightness': self.brightness}
        except Exception:
            return {'status': False, 'r': None, 'g': None, 'b': None, 'brightness': None}

    def get_all_states(self):
        return {'status': True, 'brightness': self.brightness,
                'leds': [{'r': r, 'g': g, 'b': b} for (r, g, b) in self.last_colors]}

    def set_pixel(self, led_number, r, g, b):
        return self.set_led(led_number, r, g, b)

    def set_pixel_rgb(self, led_number, rgb):
        try:
            if isinstance(rgb, (list, tuple)) and len(rgb) >= 3:
                return self.set_led(led_number, int(rgb[0]), int(rgb[1]), int(rgb[2]))
        except Exception:
            pass
        return False

    def set_pixel_off(self, led_number):
        return self.turn_off_led(led_number)
