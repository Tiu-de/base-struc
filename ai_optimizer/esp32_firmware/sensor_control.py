# sensor_control.py - Đọc DHT11, LDR (ánh sáng), Soil (độ ẩm đất)

import time
import machine
import gc

try:
    import dht
except Exception as e:
    dht = None

try:
    import config as CFG
except Exception:
    CFG = None

DEBUG_DHT = False
if CFG and isinstance(getattr(CFG, "LOGGING", None), dict):
    try:
        DEBUG_DHT = bool(CFG.LOGGING.get("ENABLE_DHT_DEBUG", False))
    except Exception:
        DEBUG_DHT = False


class SensorController:
    def __init__(self, dht_pin=8, ldr_pin=1, soil_pin=2):
        self.dht_pin = dht_pin
        self._dht = None
        self._last_dht_ms = -10000
        try:
            self._min_dht_ms = int(CFG.SENSORS.get("DHT_MIN_INTERVAL_MS", 2500)) if CFG and hasattr(CFG, "SENSORS") else 2500
        except Exception:
            self._min_dht_ms = 2500
        self._dht_fail = 0
        self._t = None
        self._h = None
        self._reads_ok = 0
        self._reads_err = 0
        self._dht_init()

        self._ldr_adc  = machine.ADC(machine.Pin(ldr_pin))
        self._soil_adc = machine.ADC(machine.Pin(soil_pin))
        try:
            self._adc_samples = int(CFG.SENSORS.get("ADC_SAMPLES", 4)) if CFG and hasattr(CFG, "SENSORS") else 4
        except Exception:
            self._adc_samples = 4
        try:
            if hasattr(self._ldr_adc, "atten"):
                self._ldr_adc.atten(machine.ADC.ATTN_11DB)
            if hasattr(self._soil_adc, "atten"):
                self._soil_adc.atten(machine.ADC.ATTN_11DB)
            if hasattr(self._ldr_adc, "width"):
                self._ldr_adc.width(machine.ADC.WIDTH_12BIT)
            if hasattr(self._soil_adc, "width"):
                self._soil_adc.width(machine.ADC.WIDTH_12BIT)
        except Exception as e:
            print("ADC config warn:", e)

    def _dht_init(self):
        if dht is None:
            return
        try:
            self._dht = dht.DHT11(machine.Pin(self.dht_pin))
            time.sleep_ms(50)
            self._dht_fail = 0
        except Exception as e:
            print("DHT11 init lỗi:", e)
            self._dht = None

    def _safe_delay(self):
        if gc.mem_free() < 16 * 1024:
            gc.collect()

    def read_dht(self):
        now = time.ticks_ms()
        if time.ticks_diff(now, self._last_dht_ms) < self._min_dht_ms:
            return self._t, self._h
        self._last_dht_ms = now
        if self._dht is None:
            self._dht_init()
            return self._t, self._h
        try:
            for attempt in (1, 2):
                try:
                    self._dht.measure()
                    self._t = self._dht.temperature()
                    self._h = self._dht.humidity()
                    self._dht_fail = 0
                    self._reads_ok += 1
                    return self._t, self._h
                except Exception:
                    self._reads_err += 1
                    if attempt == 1:
                        time.sleep_ms(80)
                    else:
                        self._dht_fail += 1
                        if self._dht_fail >= 3:
                            self._dht_init()
                            self._dht_fail = 0
                        return self._t, self._h
            return self._t, self._h
        finally:
            self._safe_delay()

    def force_read_dht(self):
        prev = self._last_dht_ms
        self._last_dht_ms = time.ticks_ms() - self._min_dht_ms - 1
        v = self.read_dht()
        self._last_dht_ms = prev
        return v

    def _read_adc_avg(self, adc, samples=None):
        n = int(samples if samples is not None else getattr(self, "_adc_samples", 4))
        if n <= 0: n = 1
        total = 0
        for _ in range(n):
            total += adc.read_u16()
        return total // n

    def read_ldr(self):
        try:
            v = self._read_adc_avg(self._ldr_adc)
            return v >> 6
        except Exception as e:
            print("Lỗi đọc LDR:", e)
            return None

    def read_soil(self):
        try:
            v = self._read_adc_avg(self._soil_adc)
            dry = 1000
            wet = 4000
            try:
                if CFG and hasattr(CFG, "SENSORS"):
                    dry = int(CFG.SENSORS.get("SOIL_DRY", dry))
                    wet = int(CFG.SENSORS.get("SOIL_WET", wet))
            except Exception:
                pass
            if dry == wet: wet = max(dry + 1, wet)
            if v <= dry: pct = 0
            elif v >= wet: pct = 100
            else: pct = int((v - dry) * 100 / (wet - dry))
            return max(0, min(100, pct))
        except Exception as e:
            print("Lỗi đọc Soil:", e)
            return None

    def read_soil_raw(self):
        try:
            return self._read_adc_avg(self._soil_adc)
        except Exception as e:
            print("Lỗi đọc Soil raw:", e)
            return None
