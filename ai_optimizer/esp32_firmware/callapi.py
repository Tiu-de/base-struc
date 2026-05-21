# callapi.py - Lấy dữ liệu thời tiết từ OpenWeatherMap và AQI

import time
try:
    import urequests as requests
except ImportError:
    import requests
try:
    import ujson as json
except ImportError:
    import json

try:
    import config as CFG
except Exception:
    CFG = None


class APIControl:
    def __init__(self, aqicn_token=""):
        self.aqicn_token = aqicn_token or (
            getattr(CFG, "AQICN_TOKEN", "") if CFG else ""
        )
        # Lấy cấu hình from CFG nếu có
        if CFG:
            self.lat = float(getattr(CFG, "DEFAULT_LAT", 21.5548))
            self.lon = float(getattr(CFG, "DEFAULT_LON", 105.8439))
            self.tz = getattr(CFG, "DEFAULT_TZ", "Asia/Bangkok")
            self.weather_ttl = int(getattr(CFG, "WEATHER_TTL_SEC", 600))
            self.http_timeout = int(getattr(CFG, "HTTP_TIMEOUT_SEC", 8))
        else:
            self.lat = 21.5548
            self.lon = 105.8439
            self.tz = "Asia/Bangkok"
            self.weather_ttl = 600
            self.http_timeout = 8
        self._cache = None
        self._cache_ts = 0

    def get_weather(self, timeout=None):
        t = timeout or self.http_timeout
        now = time.time()
        if self._cache and (now - self._cache_ts) < self.weather_ttl:
            return self._cache
        result = self._fetch_open_meteo(t)
        if result:
            self._cache = result
            self._cache_ts = now
        return result

    def _fetch_open_meteo(self, timeout=8):
        url = (
            f"https://api.open-meteo.com/v1/forecast"
            f"?latitude={self.lat}&longitude={self.lon}"
            f"&current=temperature_2m,relative_humidity_2m,"
            f"wind_speed_10m,precipitation,weather_code,surface_pressure"
            f"&daily=temperature_2m_max,temperature_2m_min,"
            f"precipitation_sum,sunshine_duration"
            f"&timezone={self.tz}"
            f"&forecast_days=3"
        )
        try:
            resp = requests.get(url, timeout=timeout)
            if resp.status_code != 200:
                return None
            raw = resp.json()
            resp.close()
            curr = raw.get("current", {})
            daily = raw.get("daily", {})
            result = {
                "city": "Ha Noi",
                "temp": float(curr.get("temperature_2m", 0)),
                "humidity": float(curr.get("relative_humidity_2m", 0)),
                "wind_speed": float(curr.get("wind_speed_10m", 0)),
                "precipitation": float(curr.get("precipitation", 0)),
                "weather_code": int(curr.get("weather_code", 0)),
                "pressure": float(curr.get("surface_pressure", 1013)),
                "daily_max": (daily.get("temperature_2m_max") or [None])[0],
                "daily_min": (daily.get("temperature_2m_min") or [None])[0],
                "daily_precip": (daily.get("precipitation_sum") or [None])[0],
                "sunshine_hr": round(
                    ((daily.get("sunshine_duration") or [None])[0] or 0) / 3600.0, 2
                ),
            }
            # Thêm AQI nếu có token
            if self.aqicn_token:
                aqi = self._fetch_aqicn(timeout)
                if aqi is not None:
                    result["aqi"] = aqi
            return result
        except Exception as e:
            print("Lỗi lấy thời tiết Open-Meteo:", e)
            return None

    def _fetch_aqicn(self, timeout=8):
        url = f"https://api.waqi.info/feed/geo:{self.lat};{self.lon}/?token={self.aqicn_token}"
        try:
            r = requests.get(url, timeout=timeout)
            if r.status_code != 200:
                r.close()
                return None
            d = r.json()
            r.close()
            if d.get("status") == "ok":
                return int(d["data"].get("aqi", 0))
        except Exception:
            pass
        return None
