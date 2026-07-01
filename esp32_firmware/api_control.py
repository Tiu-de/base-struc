# api_control.py — Weather/AQI aggregator for MicroPython
# - Preserves original flat get_weather(...) contract (OWM via callapi.py)
# - Adds Open-Meteo (om_*) and AQICN (aqi_*)
# - Now includes richer om_* fields for on-board AI features:
#   dew point, relative humidity, 180m wind & temperature, soil temp 54cm,
#   soil moisture 27–81cm (next hour + 24h aggregates)
# - Lightweight: urequests/json, ticks-based caching
# - Safe even if callapi.py is missing
# - AQICN token is hardcoded below as requested

try:
    import urequests as requests
except Exception:
    import requests  # allow desktop testing
try:
    import ujson as json
except Exception:
    import json
import time, gc
try:
    import config as CFG
except Exception:
    CFG = None

_API_MEM_DEBUG = False
if CFG and isinstance(getattr(CFG, "LOGGING", None), dict):
    try:
        _API_MEM_DEBUG = bool(CFG.LOGGING.get("ENABLE_API_MEM_DEBUG", False))
    except Exception:
        _API_MEM_DEBUG = False

# ------------------ HARD-CODED TOKENS (per user request) ------------------
AQICN_TOKEN_HARDCODED = "<REDACTED_AQICN_TOKEN>"
# --------------------------------------------------------------------------

# -------- Optional config import with safe defaults --------
try:
    import config
    _HAS_CONFIG = True
except Exception:
    _HAS_CONFIG = False

# Defaults if config.py is absent or missing fields
_DEF_LAT = getattr(config, "DEFAULT_LAT", 21.5548) if _HAS_CONFIG else 21.5548
_DEF_LON = getattr(config, "DEFAULT_LON", 105.8439) if _HAS_CONFIG else 105.8439
_DEF_TZ  = getattr(config, "DEFAULT_TZ",  "Asia/Bangkok") if _HAS_CONFIG else "Asia/Bangkok"

_OPEN_METEO_ENABLE = getattr(config, "OPEN_METEO_ENABLE", True) if _HAS_CONFIG else True
_AQICN_ENABLE      = getattr(config, "AQICN_ENABLE", True) if _HAS_CONFIG else True

_WEATHER_TTL_SEC   = getattr(config, "WEATHER_TTL_SEC", 600) if _HAS_CONFIG else 600
_HTTP_TIMEOUT_SEC  = getattr(config, "HTTP_TIMEOUT_SEC", 8) if _HAS_CONFIG else 8

# -------- Optional OWM wrapper (callapi.py) --------
# We will use callapi.get_weather(timeout=...) if available
_callapi_get_weather = None
try:
    import callapi
    if hasattr(callapi, "get_weather"):
        _callapi_get_weather = callapi.get_weather
except Exception:
    _callapi_get_weather = None


# ------------------------ internal helpers ------------------------
def _now_ms():
    try:
        return time.ticks_ms()
    except Exception:
        return int(time.time() * 1000)

def _is_fresh(ts_ms, ttl_ms):
    if not ts_ms:
        return False
    try:
        return time.ticks_diff(_now_ms(), ts_ms) < ttl_ms
    except Exception:
        return (_now_ms() - ts_ms) < ttl_ms

def _http_get(url, timeout=None):
    """
    MicroPython-friendly GET:
      - try requests.get(url, timeout=...)
      - if firmware urequests doesn't accept timeout, retry without it
    """
    try:
        if timeout is not None:
            return requests.get(url, timeout=timeout)
        return requests.get(url)
    except TypeError:
        # Some urequests builds don't accept timeout param
        return requests.get(url)

# ---------- small stats helpers ----------
def _safe_max(arr, n=24):
    return max(arr[:n]) if arr else None

def _safe_min(arr, n=24):
    return min(arr[:n]) if arr else None

def _safe_sum(arr, n=24):
    if not arr:
        return 0.0
    total = 0.0
    m = min(n, len(arr))
    for i in range(m):
        v = arr[i]
        if v is None:
            v = 0.0
        total += v
    return total

def _safe_mean(arr, n=24):
    if not arr:
        return None
    m = min(n, len(arr))
    s = 0.0
    c = 0
    for i in range(m):
        v = arr[i]
        if v is None:
            continue
        s += v
        c += 1
    return (s / c) if c else None

# ---------------------------- main class ----------------------------
class APIControl:
    """
    Unified weather provider:
      - OWM via callapi.get_weather()  -> original fields preserved (flat)
      - Open-Meteo forecast snapshot   -> om_* keys
      - AQICN air quality              -> aqi_* keys

    Public method:
      - get_weather(timeout: int = _HTTP_TIMEOUT_SEC) -> dict

    Location/behavior can be adjusted via config.py or setters.
    """

    def __init__(self,
                 latitude=None,
                 longitude=None,
                 timezone=None,
                 enable_open_meteo=None,
                 enable_aqicn=None,
                 cache_ttl_sec=None,
                 http_timeout_sec=None,
                 aqicn_token="<REDACTED_AQICN_TOKEN>"):
        # Configurable parameters (fallback to defaults)
        self.lat = float(latitude)  if latitude  is not None else float(_DEF_LAT)
        self.lon = float(longitude) if longitude is not None else float(_DEF_LON)
        self.tz  = timezone  if timezone  is not None else _DEF_TZ

        self.enable_open_meteo = _OPEN_METEO_ENABLE if enable_open_meteo is None else bool(enable_open_meteo)
        self.enable_aqicn      = _AQICN_ENABLE      if enable_aqicn      is None else bool(enable_aqicn)

        self.cache_ttl_sec  = int(cache_ttl_sec)  if cache_ttl_sec  is not None else int(_WEATHER_TTL_SEC)
        self.http_timeout   = int(http_timeout_sec) if http_timeout_sec is not None else int(_HTTP_TIMEOUT_SEC)

        # Local caches
        self._cache_owm = {"ts": 0, "data": None, "err": None}
        self._cache_om  = {"ts": 0, "data": None, "err": None}
        self._cache_aqi = {"ts": 0, "data": None, "err": None}

        # Last combined error (human-readable)
        self.last_error = None

        # AQICN token precedence: explicit param > hardcoded
        self._aqicn_token = aqicn_token or AQICN_TOKEN_HARDCODED

    # ---------- Public API ----------
    def set_location(self, latitude, longitude, timezone=None):
        """Update the default location/timezone used by providers."""
        self.lat = float(latitude)
        self.lon = float(longitude)
        if timezone:
            self.tz = timezone

    def get_weather(self, timeout=None):
        """
        Return a flat dict that may contain:
          - OWM fields (if callapi.get_weather() available) with original keys
          - Open-Meteo om_* fields (if enabled)
          - AQICN aqi_* fields (if enabled)
        NOTE: If callapi.get_weather() is missing or fails, all api_* and weather_* keys will be absent.
        """
        to = int(timeout) if (timeout is not None) else self.http_timeout
        ttl = self.cache_ttl_sec

        out = {}
        errs = []

        # 1) OWM via callapi (original behavior)
        owm = self._get_owm_cached(ttl, to)
        if owm is not None:
            # Preserve original keys (whatever callapi returns)
            out.update(owm)
        else:
            if self._cache_owm["err"]:
                errs.append("owm: " + self._cache_owm["err"])

        # 2) Open-Meteo lightweight snapshot (+ richer features for AI)
        if self.enable_open_meteo:
            om = self._get_open_meteo_cached(ttl, to)
            if om is not None:
                out.update(om)  # prefixed om_*
            else:
                if self._cache_om["err"]:
                    errs.append("openmeteo: " + self._cache_om["err"])

        # 3) AQICN
        if self.enable_aqicn:
            aqi = self._get_aqicn_cached(ttl, to)
            if aqi is not None:
                out.update(aqi)  # prefixed aqi_*
            else:
                if self._cache_aqi["err"]:
                    errs.append("aqicn: " + self._cache_aqi["err"])

        # Record combined error (if any) but never raise; downstream remains stable
        self.last_error = "; ".join(errs) if errs else None

        return out

    # ---------- OWM (callapi.py) ----------
    def _get_owm_cached(self, ttl_sec, timeout):
        c = self._cache_owm
        if _is_fresh(c["ts"], ttl_sec * 1000) and c["data"] is not None:
            return c["data"]
        if _callapi_get_weather is None:
            c["err"] = "callapi.get_weather() not found"
            c["ts"] = _now_ms()
            c["data"] = None
            return None
        try:
            data = _callapi_get_weather(timeout=timeout)
            if not isinstance(data, dict):
                raise ValueError("callapi.get_weather returned non-dict")
            c["data"] = data
            c["err"] = None
        except Exception as e:
            c["data"] = None
            c["err"] = str(e)
        c["ts"] = _now_ms()
        return c["data"]

    # ---------- Open-Meteo ----------
    def _get_open_meteo_cached(self, ttl_sec, timeout):
        c = self._cache_om
        if _is_fresh(c["ts"], ttl_sec * 1000) and c["data"] is not None:
            return c["data"]
        try:
            data = self._fetch_open_meteo(self.lat, self.lon, self.tz, timeout)
            c["data"] = data
            c["err"] = None
        except Exception as e:
            c["data"] = None
            c["err"] = str(e)
        c["ts"] = _now_ms()
        return c["data"]

    def _fetch_open_meteo(self, lat, lon, timezone, timeout):
        """
        MicroPython-friendly snapshot from Open-Meteo (no heavy libs).
        Returns om_* keys only (next-hour snapshot + simple 24h aggregates).
        """
        base = "https://api.open-meteo.com/v1/forecast"
        # NOTE: wind_speed_unit (with underscore) per Open-Meteo OpenAPI
        params = (
            "hourly="
            "temperature_2m,"
            "relative_humidity_2m,"
            "dew_point_2m,"
            "precipitation,"
            "cloud_cover,"
            "wind_speed_10m,wind_direction_10m,"
            "wind_speed_180m,wind_direction_180m,"
            "temperature_180m,"
            "soil_temperature_54cm,"
            "soil_moisture_27_81cm"
            "&forecast_days=3"
            "&wind_speed_unit=ms"
            f"&latitude={lat}&longitude={lon}"
            f"&timezone={timezone}"
        )
        url = base + "?" + params
        r = None
        try:
            # Pre-fetch GC to reduce fragmentation before allocating response buffer
            try:
                gc.collect()
            except Exception:
                pass
            r = _http_get(url, timeout)
            j = r.json()
        finally:
            try:
                if r:
                    r.close()
            except Exception:
                pass
            # Post-fetch GC + optional RAM diagnostic
            try:
                gc.collect()
                mf = gc.mem_free() if hasattr(gc, 'mem_free') else None
                if mf is not None and _API_MEM_DEBUG:
                    print(f"[api] OM mem_free={mf}")
            except Exception:
                pass

        hourly = j.get("hourly") or {}
        t2m   = hourly.get("temperature_2m") or []
        rh2m  = hourly.get("relative_humidity_2m") or []
        dp2m  = hourly.get("dew_point_2m") or []
        prec  = hourly.get("precipitation")  or []
        cc    = hourly.get("cloud_cover")     or []
        ws10  = hourly.get("wind_speed_10m")  or []
        wd10  = hourly.get("wind_direction_10m") or []
        ws180 = hourly.get("wind_speed_180m") or []
        wd180 = hourly.get("wind_direction_180m") or []
        t180  = hourly.get("temperature_180m") or []
        st54  = hourly.get("soil_temperature_54cm") or []
        sm2781= hourly.get("soil_moisture_27_81cm") or []

        # Compose payload
        data = {
            # snapshot (next hour)
            "om_next_temp_c":               t2m[0]   if len(t2m)   else None,
            "om_next_rh_pct":               rh2m[0]  if len(rh2m)  else None,
            "om_next_dewpoint_c":           dp2m[0]  if len(dp2m)  else None,
            "om_next_precip_mm":            prec[0]  if len(prec)  else None,
            "om_next_cloudcover_pct":       cc[0]    if len(cc)    else None,
            "om_next_windspeed_ms":         ws10[0]  if len(ws10)  else None,
            "om_next_winddir_deg":          wd10[0]  if len(wd10)  else None,
            "om_next_windspeed_180m_ms":    ws180[0] if len(ws180) else None,
            "om_next_winddir_180m_deg":     wd180[0] if len(wd180) else None,
            "om_next_temp_180m_c":          t180[0]  if len(t180)  else None,
            "om_next_soil_temp_54cm_c":     st54[0]  if len(st54)  else None,
            "om_next_soil_moisture_27_81cm_m3m3": sm2781[0] if len(sm2781) else None,

            # 24h aggregates (first 24 values)
            "om_sum_precip_24h_mm":         _safe_sum(prec, 24),
            "om_tmax_24h_c":                _safe_max(t2m, 24),
            "om_tmin_24h_c":                _safe_min(t2m, 24),
            "om_windmax_24h_ms":            _safe_max(ws10, 24),
            "om_cloud_mean_24h_pct":        _safe_mean(cc, 24),

            # 48h aggregates (hours 24-47, ngày thứ 2)
            "om_sum_precip_48h_mm":         _safe_sum(prec[24:], 24),
            "om_tmax_48h_c":                _safe_max(t2m[24:], 24),
            "om_tmin_48h_c":                _safe_min(t2m[24:], 24),

            # 72h aggregates (hours 48-71, ngày thứ 3)
            "om_sum_precip_72h_mm":         _safe_sum(prec[48:], 24),
            "om_tmax_72h_c":                _safe_max(t2m[48:], 24),
            "om_tmin_72h_c":                _safe_min(t2m[48:], 24),

            # new 24h aggregates useful for AI
            "om_rh_mean_24h_pct":           _safe_mean(rh2m, 24),
            "om_dewpoint_min_24h_c":        _safe_min(dp2m, 24),
            "om_dewpoint_max_24h_c":        _safe_max(dp2m, 24),
            "om_soil_temp_54cm_mean_24h_c": _safe_mean(st54, 24),
            "om_soil_moisture_27_81cm_mean_24h": _safe_mean(sm2781, 24),

            # meta
            "om_lat": lat,
            "om_lon": lon,
            "om_timezone": j.get("timezone"),
        }
        return data

    # ---------- AQICN ----------
    def _get_aqicn_cached(self, ttl_sec, timeout):
        c = self._cache_aqi
        if _is_fresh(c["ts"], ttl_sec * 1000) and c["data"] is not None:
            return c["data"]
        try:
            data = self._fetch_aqicn(self.lat, self.lon, self._aqicn_token, timeout)
            c["data"] = data
            c["err"] = None
        except Exception as e:
            c["data"] = None
            c["err"] = str(e)
        c["ts"] = _now_ms()
        return c["data"]

    def _fetch_aqicn(self, lat, lon, token, timeout):
        """
        Returns aqi_* keys. If token missing or API error, returns minimal dict.
        """
        if not token:
            return {"aqi_status": "no_token"}

        #url = "https://api.waqi.info/feed/here/?token=3dc16dd3108a851aef263ca412729a99cdd84a69".format(lat, lon, token)
        url = "https://api.waqi.info/feed/geo:{:f};{:f}/?token={}".format(lat, lon, token)
        r = None
        try:
            try:
                gc.collect()
            except Exception:
                pass
            r = _http_get(url, timeout)
            j = r.json()
        finally:
            try:
                if r:
                    r.close()
            except Exception:
                pass
            try:
                gc.collect()
                mf = gc.mem_free() if hasattr(gc, 'mem_free') else None
                if mf is not None and _API_MEM_DEBUG:
                    print(f"[api] AQI mem_free={mf}")
            except Exception:
                pass

        if j.get("status") != "ok":
            return {"aqi_status": j.get("status", "error")}

        d = j.get("data") or {}
        iaqi = d.get("iaqi") or {}

        def _v(key):
            x = iaqi.get(key) or {}
            return x.get("v")

        data = {
            "aqi":          d.get("aqi"),
            "aqi_dominent": d.get("dominentpol"),
            "aqi_pm25":     _v("pm25"),
            "aqi_pm10":     _v("pm10"),
            "aqi_o3":       _v("o3"),
            "aqi_no2":      _v("no2"),
            "aqi_so2":      _v("so2"),
            "aqi_co":       _v("co"),
            "aqi_station":  (d.get("city") or {}).get("name"),
            "aqi_time_iso": (d.get("time") or {}).get("iso") or (d.get("time") or {}).get("s"),
            "aqi_status":   "ok",
            "aqi_lat":      lat,
            "aqi_lon":      lon,
        }
        # Bổ sung các trường forecast, city, attributions, debug nếu có
        if "forecast" in d:
            data["aqi_forecast"] = d["forecast"]
        if "city" in d:
            data["aqi_city"] = d["city"]
        if "attributions" in d:
            data["aqi_attributions"] = d["attributions"]
        if "debug" in d:
            data["aqi_debug"] = d["debug"]
        # Nếu muốn lấy toàn bộ iaqi gốc:
        data["aqi_iaqi"] = iaqi
        return data
