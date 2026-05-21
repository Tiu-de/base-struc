# soil_monitor.py - Monitor ADC soil realtime trên ESP32 (MicroPython)
# Chạy trực tiếp trên thiết bị để xem giá trị ADC raw cho calibration

import time
import machine
import gc

try:
    import config as CFG
except Exception:
    CFG = None

def monitor_soil_adc(duration_sec=60, interval_ms=1000):
    soil_pin = 2
    dry_val = 1000
    wet_val = 6000
    try:
        if CFG and hasattr(CFG, "SENSORS"):
            soil_pin = int(CFG.SENSORS.get("SOIL_PIN", soil_pin))
            dry_val = int(CFG.SENSORS.get("SOIL_DRY", dry_val))
            wet_val = int(CFG.SENSORS.get("SOIL_WET", wet_val))
    except Exception:
        pass
    try:
        adc = machine.ADC(machine.Pin(soil_pin))
        if hasattr(adc, "atten"):
            adc.atten(machine.ADC.ATTN_11DB)
        if hasattr(adc, "width"):
            adc.width(machine.ADC.WIDTH_12BIT)
    except Exception as e:
        print(f"Lỗi init ADC: {e}")
        return
    min_adc = 65535
    max_adc = 0
    count = 0
    start_time = time.time()
    try:
        while True:
            if duration_sec > 0 and (time.time() - start_time) >= duration_sec:
                break
            total = 0
            for _ in range(4):
                total += adc.read_u16()
            raw = total // 4
            if raw < min_adc: min_adc = raw
            if raw > max_adc: max_adc = raw
            count += 1
            if raw <= dry_val: pct = 0
            elif raw >= wet_val: pct = 100
            else: pct = int((raw - dry_val) * 100 / (wet_val - dry_val))
            pct = max(0, min(100, pct))
            time.sleep_ms(interval_ms)
            if count % 10 == 0: gc.collect()
    except KeyboardInterrupt:
        pass

def quick_test():
    monitor_soil_adc(duration_sec=10, interval_ms=500)

def continuous_monitor():
    monitor_soil_adc(duration_sec=0, interval_ms=1000)

def test(): quick_test()
def monitor(): continuous_monitor()
