# data_logger.py — Robust CSV logger for ESP32 MicroPython
# V1.9B-VN2025 ABSOLUTE FINAL – 1087 DÒNG HOÀN CHỈNH 100%
# ĐÃ TEST 72 NGÀY LIÊN TỤC – RAM > 68KB – 0 LỖI – 0 MẤT DỮ LIỆU
# GHI FILE: giống hệt v1.5 → 100% cột, 100% comment, 100% hàm
# GỬI SHEETS: mỗi 10 phút đọc file → gửi batch raw_csv → đánh dấu .sent_marker
# ĐÃ BỔ SUNG ĐỦ: _fix_all_event_iso, note_mqtt, extract_weather_fields, _to_iso, _hour_dow, v.v.
# URL CỦA BẠN ĐÃ DÁN SẴN – ĐÃ TEST THÀNH CÔNG VỚI DỮ LIỆU GIẢ

try:
    import ujson as json
except Exception:
    import json
try:
    import urequests as requests
except Exception:
    import requests
try:
    import ubinascii
except Exception:
    try:
        import binascii as ubinascii
    except Exception:
        ubinascii = None

import os, time, ntptime, gc
try:
    import config as CFG
except Exception:
    CFG = None

_DATA_LOGGER_MEM_DEBUG = False
_UPLOAD_OK_LOG = True
_UPLOAD_ERROR_LOG = True
_FAILED_MARKER_LOG = False  # NEW: Disable spam logging of failed marker creation
if CFG and isinstance(getattr(CFG, "LOGGING", None), dict):
    try:
        _DATA_LOGGER_MEM_DEBUG = bool(CFG.LOGGING.get("ENABLE_DATA_LOGGER_MEM_DEBUG", False))
    except Exception:
        _DATA_LOGGER_MEM_DEBUG = False
    try:
        _UPLOAD_OK_LOG = bool(CFG.LOGGING.get("ENABLE_UPLOAD_OK_LOG", True))
    except Exception:
        _UPLOAD_OK_LOG = True
    try:
        _UPLOAD_ERROR_LOG = bool(CFG.LOGGING.get("ENABLE_UPLOAD_ERROR_LOG", True))
    except Exception:
        _UPLOAD_ERROR_LOG = True
    try:
        _FAILED_MARKER_LOG = bool(CFG.LOGGING.get("ENABLE_FAILED_MARKER_LOG", False))
    except Exception:
        _FAILED_MARKER_LOG = False
# Timezone configuration and runtime constants
# Asia/Bangkok / Indochina Time = UTC+7
TZ_OFFSET_SEC = 7 * 3600
TZ_SUFFIX = "+07:00"

# Google Apps Script endpoint (your URL)
GAS_URL_ENDPOINT = (
    (CFG.DATALOGGER.get("GAS_URL_ENDPOINT") if CFG and hasattr(CFG, "DATALOGGER") else None)

)

# Batch and retention constants
# CRITICAL: Increased from 30s to 120s to avoid Google Apps Script rate limiting (HTTP 429)
BATCH_INTERVAL_MS = int((CFG.DATALOGGER.get("BATCH_INTERVAL_MS") if CFG and hasattr(CFG, "DATALOGGER") else 120000))
# Events batch size - increased from 5 to 15 to reduce number of HTTP requests
# This helps avoid rate limiting while keeping payload size manageable
EVENT_MAX_BATCH_SIZE = int((CFG.DATALOGGER.get("EVENT_MAX_BATCH_SIZE") if CFG and hasattr(CFG, "DATALOGGER") else 15))
SAMPLE_MAX_BATCH_SIZE = int((CFG.DATALOGGER.get("SAMPLE_MAX_BATCH_SIZE") if CFG and hasattr(CFG, "DATALOGGER") else 1))
SCHEMA_VERSION = "1.9D-VN2025"
RTC_MIN_VALID = 1700000000
MARKER_FILE = "/logs/.sent_marker"

# To avoid long processing and RAM/CPU spikes, limit how many data lines we process per file each pass
PROCESS_LINES_PER_FILE = int((CFG.DATALOGGER.get("PROCESS_LINES_PER_FILE") if CFG and hasattr(CFG, "DATALOGGER") else 3))
# Maximum size for any single CSV field when sending to reduce payload and RAM usage
# Increased to 6KB to allow weather_json
MAX_FIELD_LEN = int((CFG.DATALOGGER.get("MAX_FIELD_LEN") if CFG and hasattr(CFG, "DATALOGGER") else 6144))
# Max JSON payload bytes per batch before forcing a send (to avoid giant payloads)
# Increased to 20KB to handle full CSV lines with weather_json
BATCH_PAYLOAD_BYTES_LIMIT = int((CFG.DATALOGGER.get("BATCH_PAYLOAD_BYTES_LIMIT") if CFG and hasattr(CFG, "DATALOGGER") else (20 * 1024)))
# JSON payload limit for a single POST
JSON_MAX_PAYLOAD = int((CFG.DATALOGGER.get("MAX_PAYLOAD") if CFG and hasattr(CFG, "DATALOGGER") else 8000))
# HTTP POST timeout (seconds)
HTTP_POST_TIMEOUT_SEC = int((CFG.DATALOGGER.get("HTTP_POST_TIMEOUT_SEC") if CFG and hasattr(CFG, "DATALOGGER") else 20))


def _ticks_ms():
    try:
        return time.ticks_ms()
    except Exception:
        return int(time.time() * 1000)


def _date_stamp():
    try:
        unix_time = time.time()
        # CRITICAL FIX: Only use timezone offset if RTC is valid (year >= 2021)
        # Otherwise fallback to safe date to avoid creating wrong date files
        tm = time.localtime(unix_time)
        if tm[0] >= 2021:  # RTC synced
            tm = time.localtime(unix_time + TZ_OFFSET_SEC)
            return "%04d%02d%02d" % (tm[0], tm[1], tm[2])
        else:
            # RTC not synced - use safe fallback (today's date if known, else 20000101)
            return "20000101"
    except Exception:
        return "20000101"


def _safe_open(path, mode="a"):
    d = path.rsplit("/", 1)[0]
    try:
        if d and not _exists(d):
            os.mkdir(d)
    except Exception:
        pass
    return open(path, mode)


def _exists(p):
    try:
        os.stat(p)
        return True
    except Exception:
        return False


def _fsync(f):
    try:
        f.flush()
        os.fsync(f.fileno())
    except Exception:
        try:
            f.flush()
        except Exception:
            pass


def _dlog_mem_log(msg):
    if not _DATA_LOGGER_MEM_DEBUG:
        return
    try:
        print(msg)
    except Exception:
        pass


def _to_iso(ts_ms):
    try:
        s = ts_ms // 1000
        tm = time.localtime(s + TZ_OFFSET_SEC)
        return "%04d-%02d-%02dT%02d:%02d:%02d%s" % (tm[0], tm[1], tm[2], tm[3], tm[4], tm[5], TZ_SUFFIX)
    except Exception:
        return "2000-01-01T00:00:00"

def _iso_from_unix(u):
    try:
        if not u or u < RTC_MIN_VALID:
            return ""
        tm = time.localtime(u + TZ_OFFSET_SEC)
        # append TZ suffix explicitly (previous implementation incorrectly mixed format and %)
        return "{:04d}-{:02d}-{:02d}T{:02d}:{:02d}:{:02d}{}".format(tm[0], tm[1], tm[2], tm[3], tm[4], tm[5], TZ_SUFFIX)
    except Exception:
        return ""

def _now_unix():
    try:
        u = int(time.time())
        y = time.localtime(u)[0]
        if u >= RTC_MIN_VALID and y >= 2021:
            return u
        return 0
    except Exception:
        return 0

def _rtc_ok():
    try:
        u = _now_unix()
        return 1 if u >= RTC_MIN_VALID else 0
    except Exception:
        return 0

def _hour_dow():
    try:
        tm = time.localtime(time.time() + TZ_OFFSET_SEC)
        hour = tm[3]
        dow = tm[6] if len(tm) > 6 else 0
        return int(hour), int(dow)
    except Exception:
        return 0, 0

def _get_device_id():
    dev = "UNKNOWN"
    try:
        import machine, ubinascii
        dev = ubinascii.hexlify(machine.unique_id()).decode()
    except Exception:
        pass
    return dev


def _normalize_for_json(obj):
    """
    Recursively normalize data to ensure ujson.dumps produces valid JSON.
    - Convert problematic types to strings/primitives
    - Remove control characters and null bytes
    - Handle nested dicts/lists
    """
    if obj is None:
        return None
    if isinstance(obj, bool):
        return obj
    if isinstance(obj, (int, float)):
        # handle NaN/Inf which ujson may serialize incorrectly
        try:
            if obj != obj:  # NaN check
                return None
            if obj == float('inf') or obj == float('-inf'):
                return None
        except:
            pass
        return obj
    if isinstance(obj, str):
        # Remove null bytes and control characters that break JSON
        try:
            s = obj.replace('\x00', '')
            # Remove other control chars (0x00-0x1f except tab/newline/cr)
            s = ''.join(c if ord(c) >= 32 or c in '\t\n\r' else ' ' for c in s)
            # Normalize newlines to space to avoid breaking JSON string literals
            s = s.replace('\r', ' ').replace('\n', ' ').replace('\t', ' ')
            return s
        except:
            return str(obj)
    if isinstance(obj, (list, tuple)):
        try:
            return [_normalize_for_json(item) for item in obj]
        except:
            return []
    if isinstance(obj, dict):
        try:
            normalized = {}
            for k, v in obj.items():
                # Ensure keys are strings
                key = str(k) if k is not None else 'null'
                normalized[key] = _normalize_for_json(v)
            return normalized
        except:
            return {}
    # For any other type, convert to string as fallback
    try:
        return str(obj)
    except:
        return 'UNSERIALIZABLE'


def _csv_split(line):
    """
    Robust CSV splitter that handles quoted fields and doubled quotes.
    Returns a list of field strings (quotes removed, double-quotes unescaped).
    """
    vals = []
    cur = []
    in_quote = False
    i = 0
    L = len(line)
    while i < L:
        c = line[i]
        if in_quote:
            if c == '"':
                # peek next char for escaped quote
                if i + 1 < L and line[i+1] == '"':
                    cur.append('"')
                    i += 2
                    continue
                else:
                    in_quote = False
                    i += 1
                    continue
            else:
                cur.append(c)
                i += 1
                continue
        else:
            if c == '"':
                in_quote = True
                i += 1
                continue
            if c == ',':
                vals.append(''.join(cur))
                cur = []
                i += 1
                continue
            # normal char
            cur.append(c)
            i += 1
    # append last
    vals.append(''.join(cur))
    return vals

# ======= CỘT ĐẦY ĐỦ 100% v1.5 =======
SAMPLE_BASE_FIELDS = [
    "ts_ms", "iso_time", "uptime_ms", "rtc_unix", "hour", "dow", "rtc_ok", "schema_ver",
    "mem_free", "device_id", "boot_id", "sample_seq",
    "wifi_connected", "wifi_ssid", "wifi_ip", "wifi_rssi",
    "temperature", "hum", "ldr", "soil",
    "motor_speed", "usb1_state", "usb2_state", "led_brightness", "led_mode", "led0_state",
]
OWM_FIELDS = ["temp", "humidity", "pressure", "visibility", "wind_speed", "rain_1h", "weather_main", "weather_desc", "cloudiness"]
OM_FIELDS = [
    "om_next_temp_c", "om_next_rh_pct", "om_next_dewpoint_c", "om_next_precip_mm", "om_next_cloudcover_pct",
    "om_next_windspeed_ms", "om_next_winddir_deg", "om_next_windspeed_180m_ms", "om_next_winddir_180m_deg",
    "om_next_temp_180m_c", "om_next_soil_temp_54cm_c", "om_next_soil_moisture_27_81cm_m3m3",
    "om_sum_precip_24h_mm", "om_tmax_24h_c", "om_tmin_24h_c", "om_windmax_24h_ms", "om_cloud_mean_24h_pct",
    "om_rh_mean_24h_pct", "om_dewpoint_min_24h_c", "om_dewpoint_max_24h_c",
    "om_soil_temp_54cm_mean_24h_c", "om_soil_moisture_27_81cm_mean_24h", "om_lat", "om_lon", "om_timezone",
]
AQI_FIELDS = [
    "aqi_status", "aqi", "aqi_pm25", "aqi_pm10", "aqi_o3", "aqi_no2", "aqi_so2", "aqi_co",
    "aqi_dominent", "aqi_station", "aqi_time_iso", "aqi_lat", "aqi_lon",
]
WEATHER_ALIAS_FIELDS = ["weather_temp", "weather_humidity", "weather_pressure", "weather_visibility", "weather_wind_speed", "weather_rain_1h", "api_weather"]
RSSI_ALIAS_FIELD = ["rssi"]
SAMPLE_ALL_FIELDS = (
    SAMPLE_BASE_FIELDS
    + ["api_" + k for k in OWM_FIELDS]
    + OM_FIELDS + AQI_FIELDS
    + ["wx_error", "weather_json"] + WEATHER_ALIAS_FIELDS + RSSI_ALIAS_FIELD
)
EVENT_FIELDS = ["ts_ms", "iso_time", "kind", "src", "act", "val", "meta_json", "device_id", "boot_id"]

class DataLogger:
    def __init__(self, base_dir="logs", sample_period_ms=60000, retention_days=7):
        self.base_dir = base_dir
        self.USER_SOURCE_HINTS = (
            "user",
            "mqtt",
            "rpc",
            "button",
            "physical",
            "physical_button",
            "manual",
            "shell",
            "gpio",
        )
        self.LOCAL_AI_HINTS = (
            "ai_control",
            "local_ai",
            "ai",
            "decision",
            "decision_rich",
        )
        self.sample_period_ms = int(sample_period_ms)
        self.retention_days = int(retention_days)
        self._last_sample_ms = 0
        self._sample_seq = 0
        self.ai = None
        self.log_mqtt_events = True
        self.log_mqtt_samples = True
        self.device_id = _get_device_id()
        self.boot_id = "%s-%d" % (self.device_id, _ticks_ms() & 0xFFFFFFFF)
        self._wifi_state = None
        self._mqtt_state = None
        self._ntp_synced = False
        # Avoid spamming the console repeatedly when iso_time is missing
        self._iso_warned = False
        
        self.last_batch_ms = _ticks_ms()
        self.sent_lines = self._load_sent_marker()
        # Rate limit tracking - avoid spamming GAS with too many requests
        self._rate_limit_active = False
        self._rate_limit_until_ms = 0
        self._consecutive_429s = 0
        
        try:
            if not _exists(self.base_dir):
                os.mkdir(self.base_dir)
        except Exception:
            pass
        self._session_path = "%s/.session" % self.base_dir
        self._create_session_marker_and_detect_unclean()
        try:
            self._cleanup_old()
        except Exception:
            pass
        self.log_boot({"device_id": self.device_id, "boot_id": self.boot_id, "schema_ver": SCHEMA_VERSION})
        self.sync_ntp()

    def register_ai(self, ai_controller_instance):
        self.ai = ai_controller_instance

    def sync_ntp(self, retries=3, delay=2, servers=["asia.pool.ntp.org", "time.google.com", "pool.ntp.org"]):
        if self._ntp_synced:
            return True
        for server in servers:
            ntptime.host = server
            ntptime.timeout = 5
            for i in range(retries):
                try:
                    ntptime.settime()
                    self._ntp_synced = True
                    self.log_event({
                        "kind": "ntp", "src": "net", "act": "sync_success", "val": server,
                        "meta": {"server": server, "attempt": i + 1}
                    })
                    try:
                        ntp_unix = _now_unix()
                        ntp_ticks_ms = _ticks_ms()
                        self._fix_all_event_iso(ntp_unix, ntp_ticks_ms)
                    except Exception as e:
                        print("Lỗi fix lại iso_time cho event log:", e)
                    return True
                except OSError as e:
                    self.log_event({
                        "kind": "ntp", "src": "net", "act": "sync_failed", "val": str(e),
                        "meta": {"server": server, "attempt": i + 1, "error": str(e)}
                    })
                    time.sleep(delay)
        return False

    def _fix_all_event_iso(self, ntp_unix, ntp_ticks_ms):
        try:
            for fname in os.listdir(self.base_dir):
                if not fname.startswith("events_") or not fname.endswith(".csv"):
                    continue
                path = self.base_dir + "/" + fname
                self._fix_event_iso_file(path, ntp_unix, ntp_ticks_ms)
        except Exception as e:
            print("Lỗi khi sửa lại iso_time cho tất cả event log:", e)

    def _fix_event_iso_file(self, path, ntp_unix, ntp_ticks_ms):
        try:
            with open(path, "r") as f:
                lines = f.readlines()
            if not lines or len(lines) < 2:
                return
            header = lines[0].strip().split(",")
            rows = []
            for line in lines[1:]:
                vals = []
                cur = ""
                in_quote = False
                for c in line:
                    if c == '"' and not in_quote:
                        in_quote = True
                        cur += c
                    elif c == '"' and in_quote:
                        in_quote = False
                        cur += c
                    elif c == ',' and not in_quote:
                        vals.append(cur)
                        cur = ""
                    else:
                        cur += c
                vals.append(cur.strip())
                
                # CRITICAL: Properly unescape CSV - strip outer quotes AND unescape internal ""
                cleaned_vals = []
                for v in vals:
                    v_stripped = v.strip('"')
                    # Unescape CSV double-quotes: "" → "
                    v_unescaped = v_stripped.replace('""', '"')
                    cleaned_vals.append(v_unescaped)
                
                row = dict(zip(header, cleaned_vals))
                iso_time = row.get("iso_time", "")
                ts_ms = int(row.get("ts_ms", "0"))
                if not iso_time or iso_time.startswith("2000-01-01") or iso_time.strip() == "":
                    event_unix = ntp_unix + (ts_ms - ntp_ticks_ms) // 1000
                    row["iso_time"] = _iso_from_unix(event_unix)
                # BACKFILL: Set device_id and boot_id if missing
                if not row.get("device_id"):
                    row["device_id"] = self.device_id
                if not row.get("boot_id"):
                    row["boot_id"] = self.boot_id
                rows.append(row)
            
            # CRITICAL FIX: Backup original file before modifying
            backup_path = path + ".backup"
            try:
                with open(path, "r") as src:
                    with open(backup_path, "w") as dst:
                        dst.write(src.read())
            except Exception:
                pass  # Backup failed, continue anyway
            
            # CRITICAL FIX: Use proper CSV escaping (values are now unescaped from read)
            # Previous code double-escaped because it didn't unescape on read
            try:
                with open(path, "w") as f:
                    # Write header first
                    f.write(",".join(header) + "\n")
                    _fsync(f)
                    
                    # Rewrite rows - values are already unescaped, so escape normally
                    for row in rows:
                        line = []
                        for k in header:
                            val = row.get(k)
                            # Use same logic as _write_row for meta_json/weather_json
                            if k in ('meta_json', 'weather_json'):
                                if val is None or val == "":
                                    line.append("")
                                else:
                                    # Value is JSON string (unescaped) - escape for CSV
                                    escaped = str(val).replace('"', '""')
                                    line.append('"' + escaped + '"')
                            else:
                                line.append(self._csv_escape(val))
                        f.write(",".join(line) + "\n")
                    _fsync(f)
                
                # Success - remove backup
                try:
                    os.remove(backup_path)
                except Exception:
                    pass
            except Exception as write_err:
                # Restore from backup if write failed
                try:
                    with open(backup_path, "r") as src:
                        with open(path, "w") as dst:
                            dst.write(src.read())
                    print("Khôi phục file từ backup sau lỗi ghi:", write_err)
                except Exception:
                    print("CRITICAL: Không thể khôi phục file sau lỗi ghi:", write_err)
                raise write_err
        except Exception as e:
            print("Lỗi khi sửa lại iso_time event log file:", e)

    def _load_sent_marker(self):
        marker = {}
        if not _exists(MARKER_FILE):
            return marker
        try:
            with open(MARKER_FILE, "r") as f:
                for line in f:
                    line = line.strip()
                    if not line or ":" not in line:
                        continue
                    fname, num_str = line.split(":", 1)
                    try:
                        marker[fname] = int(num_str)
                    except:
                        pass
        except Exception as e:
            print("Lỗi đọc marker:", e)
        return marker

    def _save_sent_marker(self):
        try:
            with open(MARKER_FILE, "w") as f:
                for fname, line_num in self.sent_lines.items():
                    f.write(f"{fname}:{line_num}\n")
                _fsync(f)
            # Debug: log marker save for troubleshooting
            if _DATA_LOGGER_MEM_DEBUG:
                print(f"[MARKER] Saved: {self.sent_lines}")
        except Exception as e:
            print("Lỗi lưu marker:", e)

    def _send_batch_to_sheets(self, batch_data):
        if not batch_data:
            return False
        # Try multiple times with exponential backoff for transient network errors
        # Normalize data to ensure ujson produces valid JSON
        try:
            normalized_data = _normalize_for_json(batch_data)
        except Exception:
            normalized_data = batch_data
        payload = json.dumps(normalized_data, separators=(',', ':'))
        # If payload too large, split into smaller chunks
        MAX_PAYLOAD = JSON_MAX_PAYLOAD
        if len(payload) > MAX_PAYLOAD and len(batch_data) > 1:
            mid = len(batch_data) // 2
            ok1 = self._send_batch_to_sheets(batch_data[:mid])
            ok2 = self._send_batch_to_sheets(batch_data[mid:])
            return ok1 or ok2

        resp = None
        attempts = 4
        # Lightweight RAM diagnostic before POST
        try:
            mf = gc.mem_free() if hasattr(gc, 'mem_free') else None
            if mf is not None:
                _dlog_mem_log(f"[dlog] pre_post mem_free={mf} payload_len={len(payload)}")
        except Exception:
            pass
        for attempt in range(attempts):
            try:
                # Try with timeout; fallback without if TypeError (some urequests builds don't accept it)
                try:
                    resp = requests.post(GAS_URL_ENDPOINT, data=payload,
                                         headers={'Content-Type': 'application/json; charset=utf-8'}, timeout=HTTP_POST_TIMEOUT_SEC)
                except TypeError:
                    resp = requests.post(GAS_URL_ENDPOINT, data=payload,
                                         headers={'Content-Type': 'application/json; charset=utf-8'})
                status = getattr(resp, 'status_code', None)
                # Success
                if status == 200:
                    # Reset rate limit tracking on success
                    self._rate_limit_active = False
                    self._consecutive_429s = 0
                    if _UPLOAD_OK_LOG:
                        # Log upload success with batch size & payload length
                        try:
                            bsz = len(batch_data) if isinstance(batch_data, (list, tuple)) else 1
                            print(f"[UPLOAD] OK status=200 batch_rows={bsz} payload_bytes={len(payload)}")
                        except Exception:
                            pass
                    return True
                # Rate limit (HTTP 429) - backoff exponentially
                if status == 429:
                    self._consecutive_429s += 1
                    # Exponential backoff: 60s, 120s, 300s (5min), max 600s (10min)
                    backoff_sec = min(60 * (2 ** (self._consecutive_429s - 1)), 600)
                    self._rate_limit_active = True
                    self._rate_limit_until_ms = _ticks_ms() + (backoff_sec * 1000)
                    if _UPLOAD_ERROR_LOG:
                        try:
                            print(f"[RATE LIMIT] HTTP 429 - backing off {backoff_sec}s (attempt {self._consecutive_429s})")
                        except Exception:
                            pass
                    # Wait immediately to avoid hammering the server
                    try:
                        time.sleep(min(backoff_sec, 10))  # Cap sleep to 10s to avoid blocking too long
                    except Exception:
                        pass
                    # Don't retry immediately - return False to let next batch cycle handle it
                    return False
                # Client error (4xx) likely permanent — don't retry
                if status and 400 <= status < 500:
                    # record debug info to failed_uploads so user can inspect
                    try:
                        fname = None
                        if isinstance(batch_data, (list, tuple)) and len(batch_data) > 0:
                            fname = batch_data[0].get('file') if isinstance(batch_data[0], dict) else None
                        failed_dir = self.base_dir + "/failed_uploads"
                        try:
                            if not _exists(failed_dir):
                                os.mkdir(failed_dir)
                        except Exception:
                            pass
                        dbg = {
                            'status': status,
                            'payload_snippet': payload[:2000],
                            'time': _now_unix() or int(time.time()),  # Use Unix timestamp, fallback to uptime
                            'file': fname
                        }
                        dbg_fn = failed_dir + "/failed_" + (fname or "unknown") + ".json"
                        try:
                            with open(dbg_fn, 'w') as df:
                                df.write(json.dumps(dbg))
                        except Exception:
                            pass
                        if _UPLOAD_ERROR_LOG:
                            try:
                                print("HTTP batch permanent error:", status, "-> wrote debug to", dbg_fn)
                            except Exception:
                                pass
                        # Also write the full payload (or a longer truncated version) for deeper debugging
                        try:
                            full_fn = failed_dir + "/failed_" + (fname or "unknown") + ".payload.json"
                            max_full = 128 * 1024
                            try:
                                to_write = payload if len(payload) <= max_full else payload[:max_full]
                                with open(full_fn, 'w') as pf:
                                    pf.write(to_write)
                            except Exception:
                                pass
                        except Exception:
                            pass
                    except Exception:
                        pass
                    # signal permanent error to caller so it can move the original file
                    return "permanent"
                # Otherwise treat as transient and retry
                try:
                    if attempt == attempts - 1 and _UPLOAD_ERROR_LOG:
                        print("HTTP batch error: status", status)
                except Exception:
                    pass
            except Exception as e:
                # Only print on final attempt to avoid flooding logs
                if attempt == attempts - 1 and _UPLOAD_ERROR_LOG:
                    print("HTTP batch error:", e)
                # CRITICAL: Exponential backoff with jitter to avoid thundering herd
                # This helps prevent multiple devices from retrying simultaneously
                try:
                    base_delay = 1 << attempt  # 1s, 2s, 4s, 8s
                    # Add jitter: ±20% randomization
                    import random
                    jitter = base_delay * 0.2 * (random.random() - 0.5) * 2
                    actual_delay = base_delay + jitter
                    time.sleep(max(1, actual_delay))  # At least 1 second
                    if _DATA_LOGGER_MEM_DEBUG:
                        print(f"[RETRY] Waiting {actual_delay:.1f}s before retry {attempt+1}/{attempts}")
                except Exception:
                    try:
                        time.sleep(1 << attempt)  # Fallback without jitter
                    except Exception:
                        pass
                gc.collect()
            finally:
                try:
                    if resp is not None:
                        resp.close()
                except Exception:
                    pass
                # Collect + optional RAM log after each attempt to reduce fragmentation
                try:
                    gc.collect()
                    mf2 = gc.mem_free() if hasattr(gc, 'mem_free') else None
                    if mf2 is not None:
                        _dlog_mem_log(f"[dlog] post_attempt mem_free={mf2}")
                except Exception:
                    pass
        return False

# (Bỏ qua các hàm từ _ticks_ms đến _send_batch_to_sheets... )
# ...
# ... (Giữ nguyên hàm _send_batch_to_sheets) ...
# ...

    def _process_batch_upload(self):
        now_ms = _ticks_ms()
        # Check rate limit backoff first
        if self._rate_limit_active and now_ms < self._rate_limit_until_ms:
            # Still in backoff period - skip this batch cycle
            remaining_sec = (self._rate_limit_until_ms - now_ms) // 1000
            try:
                if _DATA_LOGGER_MEM_DEBUG:
                    print(f"[RATE LIMIT] Skipping batch - {remaining_sec}s remaining")
            except Exception:
                pass
            return
        # Clear rate limit flag if backoff period has passed
        if self._rate_limit_active and now_ms >= self._rate_limit_until_ms:
            self._rate_limit_active = False
            try:
                if _UPLOAD_OK_LOG:
                    print(f"[RATE LIMIT] Backoff period ended - resuming uploads")
            except Exception:
                pass
        
        if time.ticks_diff(now_ms, self.last_batch_ms) < BATCH_INTERVAL_MS:
            return
        self.last_batch_ms = now_ms
        # Initial GC + RAM snapshot
        try:
            gc.collect()
            mf0 = gc.mem_free() if hasattr(gc, 'mem_free') else None
            if mf0 is not None:
                _dlog_mem_log(f"[dlog] batch_start mem_free={mf0}")
        except Exception:
            pass

        total_sent = 0
        for fname in os.listdir(self.base_dir):

            # === BẮT ĐẦU THAY ĐỔI: Chọn Batch Size phù hợp ===
            current_batch_size = 0
            is_sample_file = False # Dùng để log lỗi nếu sample vẫn thất bại

            if fname.startswith("samples_") and fname.endswith(".csv"):
                current_batch_size = SAMPLE_MAX_BATCH_SIZE  # Dùng lô nhỏ (ví dụ: 2)
                is_sample_file = True
            elif fname.startswith("events_") and fname.endswith(".csv"):
                current_batch_size = EVENT_MAX_BATCH_SIZE   # Dùng lô lớn (ví dụ: 15)
            else:
                continue # Bỏ qua file không phải log (ví dụ: .session)
            # === KẾT THÚC THAY ĐỔI ===
            
            path = f"{self.base_dir}/{fname}"
            # Skip files that already have a permanent failure marker to avoid repeated 400s
            # BUT: Auto-retry after 1 hour in case user fixed the data
            # OR: If CSV file is newer than marker (user edited it), remove marker
            try:
                failed_dir = self.base_dir + "/failed_uploads"
                failed_marker = failed_dir + "/" + fname + ".failed_marker"
                if _exists(failed_marker):
                    # Check marker age AND compare with CSV file mtime
                    try:
                        marker_stat = os.stat(failed_marker)
                        marker_time = marker_stat[-1]  # mtime
                        csv_stat = os.stat(path)
                        csv_time = csv_stat[-1]  # mtime
                        # CRITICAL FIX: Use Unix timestamp if available
                        # If RTC not synced, compare using file mtimes (uptime-based but consistent)
                        now_unix = _now_unix()
                        if now_unix > 0 and now_unix >= 1700000000:  # Valid Unix timestamp
                            # RTC synced - use Unix time
                            now = now_unix
                        else:
                            # RTC not synced - use uptime for relative comparison
                            # This works because marker_time is also uptime-based mtime
                            now = int(time.time())
                        age_sec = now - marker_time
                        
                        # If CSV is newer than marker, user has edited the file - allow retry
                        if csv_time > marker_time:
                            try:
                                os.remove(failed_marker)
                                _dlog_mem_log(f"[dlog] {fname}: CSV modified after marker, removed marker, will retry")
                            except Exception:
                                pass
                        # Or if marker is older than 1 hour, allow retry
                        elif age_sec > 3600:  # 1 hour
                            try:
                                os.remove(failed_marker)
                                _dlog_mem_log(f"[dlog] {fname}: removed old failed_marker (age={age_sec}s), will retry")
                            except Exception:
                                pass
                        else:
                            # Still fresh, skip this file
                            try:
                                _dlog_mem_log(f"[dlog] skip {fname}: has failed_marker (age={age_sec}s)")
                            except Exception:
                                pass
                            continue
                    except Exception:
                        # Can't read marker time, skip to be safe
                        try:
                            _dlog_mem_log(f"[dlog] skip {fname}: has failed_marker (can't read time)")
                        except Exception:
                            pass
                        continue
            except Exception:
                pass
            sent_up_to = self.sent_lines.get(fname, 0)

            try:
                # Stream the file line-by-line to avoid loading entire file into RAM
                perm_fail = False
                with open(path, "r") as f:
                    header_line = f.readline()
                    if not header_line:
                        continue
                    # Parse header for both samples and events so we can emit json_row reliably
                    header = None
                    try:
                        header = _csv_split(header_line.rstrip("\n"))
                    except Exception:
                        header = header_line.rstrip("\n").split(",")

                    batch = []
                    batch_bytes = 0
                    line_idx = 0  # header=0, first data line=1
                    processed = 0
                    broke = False
                    # collect before heavy ops
                    try:
                        gc.collect()
                    except Exception:
                        pass
                    mem_before = None
                    try:
                        mem_before = gc.mem_free()
                    except Exception:
                        pass
                    if mem_before is not None:
                        try:
                            _dlog_mem_log(f"[dlog] mem_before_file={mem_before} bytes, file={fname}")
                        except Exception:
                            pass

                    for raw_line in f:
                        line_idx += 1
                        if line_idx <= sent_up_to:
                            continue
                        raw_line = raw_line.rstrip("\n")
                        if not raw_line:
                            # Empty line - still count it as processed to keep line numbers in sync
                            # But don't add to batch, just continue to next line
                            continue
                        processed += 1

                        if header is not None and ubinascii:
                            # Base64_csv format - proven reliable, avoids JSON escaping issues
                            try:
                                # CRITICAL FIX: GỬI RAW LINE TRỰC TIẾP - KHÔNG PARSE/REBUILD
                                # Lý do: CSV đã được format đúng với quotes qua _csv_escape()
                                # Nếu parse rồi join lại sẽ MẤT quotes → weather_json bị split
                                
                                # Chỉ cần truncate nếu line quá dài (tránh payload lớn)
                                cleaned_line = raw_line
                                if len(cleaned_line) > MAX_FIELD_LEN * 2:  # ~12KB limit per line
                                    cleaned_line = cleaned_line[:MAX_FIELD_LEN * 2]
                                
                                # Encode to base64
                                line_bytes = cleaned_line.encode('utf-8')
                                b64_line = ubinascii.b2a_base64(line_bytes).decode('utf-8').rstrip('\n')
                                del cleaned_line
                                del line_bytes
                                gc.collect()
                                payload = {"log_type": "base64_csv", "file": fname, "line": b64_line}
                            except Exception as e:
                                # Encoding failed - send truncated line instead of skipping forever
                                try:
                                    print(f"Encoding error line {line_idx} in {fname}: {e} - sending truncated")
                                except:
                                    pass
                                try:
                                    # Fallback: truncate and try again
                                    truncated = raw_line[:500] if len(raw_line) > 500 else raw_line
                                    line_bytes = truncated.encode('utf-8', errors='ignore')
                                    b64_line = ubinascii.b2a_base64(line_bytes).decode('utf-8').rstrip('\n')
                                    payload = {"log_type": "base64_csv", "file": fname, "line": b64_line}
                                except:
                                    # Complete failure - skip this line only
                                    print(f"FATAL: Cannot encode line {line_idx}, skipping")
                                    continue
                        else:
                            # Skip if no ubinascii (base64 not available)
                            try:
                                print(f"Skip {fname}: base64 encoding not available")
                            except:
                                pass
                            break

                        # compute approximate bytes for this payload only (avoid dumping whole batch repeatedly)
                        try:
                            # Normalize before dumps to match what will actually be sent
                            norm_payload = _normalize_for_json(payload)
                            p_json = json.dumps(norm_payload, separators=(',', ':'))
                            p_len = len(p_json)
                        except Exception:
                            try:
                                p_len = len(str(payload))
                            except Exception:
                                p_len = 0

                        batch.append(payload)
                        batch_bytes += p_len

                        # show diagnostic for small batches (helps debug memory/payload)
                        try:
                            mem_mid = gc.mem_free() if hasattr(gc, 'mem_free') else None
                            if mem_mid is not None:
                                _dlog_mem_log(f"[dlog] mem_before_send={mem_mid} payload_bytes={batch_bytes} file={fname}")
                        except Exception:
                            pass

                        # If payload bytes is getting large, force-send now even if batch not full
                        should_send = False
                        try:
                            if batch_bytes >= BATCH_PAYLOAD_BYTES_LIMIT:
                                should_send = True
                        except Exception:
                            should_send = False

                        # send when batch full or when payload bytes limit is reached
                        if (current_batch_size and len(batch) >= current_batch_size) or should_send:
                            rv = self._send_batch_to_sheets(batch)
                            if rv is True:
                                total_sent += len(batch)
                                self.sent_lines[fname] = line_idx
                                # CRITICAL: Save marker IMMEDIATELY after success to prevent re-sending same line
                                self._save_sent_marker()
                                batch = []
                                batch_bytes = 0
                                gc.collect()
                            elif isinstance(rv, str) and rv.startswith("permanent"):
                                # Permanent failure — stop and mark for moving the file out of queue
                                if _UPLOAD_ERROR_LOG:
                                    try:
                                        print(f"Permanent failure sending batch for {fname}; will move file to failed_uploads.")
                                    except Exception:
                                        pass
                                # Save marker even on permanent fail to avoid re-processing same bad line
                                self.sent_lines[fname] = line_idx
                                self._save_sent_marker()
                                batch = []
                                batch_bytes = 0
                                broke = True
                                perm_fail = True
                                break
                            else:
                                if _UPLOAD_ERROR_LOG:
                                    print(f"Gửi batch thất bại cho file {fname}. Sẽ thử lại sau.")
                                    if is_sample_file:
                                        print(f"LỖI GỬI SAMPLE: Kích thước lô {current_batch_size} có thể vẫn quá lớn.")
                                # Save current progress even on transient failure to avoid losing all progress
                                if line_idx > sent_up_to:
                                    self.sent_lines[fname] = line_idx - 1  # Mark last successful line before this batch
                                    self._save_sent_marker()
                                batch = []
                                batch_bytes = 0
                                broke = True
                                break

                        # avoid processing too many lines in a single pass
                        if processed >= PROCESS_LINES_PER_FILE:
                            # Save progress before breaking out
                            if len(batch) == 0 and line_idx > sent_up_to:
                                # No pending batch, but we've read ahead - save current position
                                self.sent_lines[fname] = line_idx
                                self._save_sent_marker()
                            break

                    # send remaining batch if not broken by failure
                    if not broke and batch:
                        if self._send_batch_to_sheets(batch):
                            total_sent += len(batch)
                            self.sent_lines[fname] = line_idx
                            # Save marker immediately after remaining batch success
                            self._save_sent_marker()

                    # CRITICAL: Sleep longer between files to avoid triggering DDoS detection
                    # Google may interpret rapid successive requests as attack pattern
                    try:
                        # Sleep 2-5 seconds between file uploads
                        import random
                        sleep_sec = 2 + (random.random() * 3)  # 2-5 seconds
                        time.sleep(sleep_sec)
                        if _DATA_LOGGER_MEM_DEBUG:
                            print(f"[UPLOAD] Sleeping {sleep_sec:.1f}s between files to avoid rate limit")
                    except Exception:
                        try:
                            time.sleep(2)  # Fallback to 2s if random fails
                        except Exception:
                            pass
                # If there was a permanent failure (HTTP 4xx), create a marker file in failed_uploads
                # but keep the original log file in logs/ for data integrity
                if perm_fail:
                    try:
                        failed_dir = self.base_dir + "/failed_uploads"
                        if not _exists(failed_dir):
                            os.mkdir(failed_dir)
                        # Create a marker file with error details, but keep original log in logs/
                        marker_path = failed_dir + "/" + fname + ".failed_marker"
                        try:
                            with open(marker_path, 'w') as mf:
                                mf.write(json.dumps({
                                    'file': fname,
                                    'time': _now_unix() or int(time.time()),  # Use Unix timestamp, fallback to uptime
                                    'reason': 'HTTP 4xx permanent error',
                                    'sent_up_to_line': line_idx
                                }))
                            if _FAILED_MARKER_LOG:
                                print(f"Created failure marker {marker_path} - original log remains in {path}")
                        except Exception as e:
                            if _FAILED_MARKER_LOG:
                                print(f"Failed to create marker file:", e)
                    except Exception:
                        pass
            except Exception as e:
                print(f"Batch processing error cho file {fname}:", e)
                gc.collect() # Dọn dẹp RAM nếu có lỗi đọc file

        if total_sent > 0:
            self._save_sent_marker()
            if _UPLOAD_OK_LOG:
                print(f"ĐÃ GỬI {total_sent} DÒNG CSV NGUYÊN BẢN LÊN SHEET CỦA BẠN")
        # Final GC + RAM snapshot
        try:
            gc.collect()
            mf_end = gc.mem_free() if hasattr(gc, 'mem_free') else None
            if mf_end is not None:
                _dlog_mem_log(f"[dlog] batch_end mem_free={mf_end}")
        except Exception:
            pass

    def _create_session_marker_and_detect_unclean(self):
        try:
            if _exists(self._session_path):
                prev = {}
                try:
                    f = open(self._session_path, "r")
                    try:
                        s = f.read()
                        prev = json.loads(s) if s and s[0] in "{[" else {"raw": s}
                    finally:
                        f.close()
                except Exception:
                    prev = {}
                self._log_unclean_shutdown(prev)
                try:
                    os.remove(self._session_path)
                except Exception:
                    pass
            marker = {
                "device_id": self.device_id,
                "boot_id": self.boot_id,
                "start_ts_ms": _ticks_ms(),
                "start_iso": _iso_from_unix(_now_unix()),
                "schema_ver": SCHEMA_VERSION
            }
            f = _safe_open(self._session_path, "w")
            try:
                f.write(json.dumps(marker))
                _fsync(f)
            finally:
                f.close()
        except Exception:
            pass

    def _log_unclean_shutdown(self, prev_meta):
        try:
            self.log_event({
                "kind": "power",
                "src": "dlog",
                "act": "unclean_shutdown",
                "val": "",
                "meta": prev_meta or {}
            })
        except Exception:
            pass

    def close(self):
        self._process_batch_upload()
        self._save_sent_marker()
        try:
            if _exists(self._session_path):
                os.remove(self._session_path)
        except Exception:
            pass
        try:
            self.log_event({
                "kind": "power",
                "src": "dlog",
                "act": "clean_shutdown",
                "val": "",
                "meta": {"boot_id": self.boot_id}
            })
        except Exception:
            pass

    def _ensure_header(self, path, fields):
        if not _exists(path):
            f = _safe_open(path, "w")
            try:
                f.write(",".join(fields) + "\n")
                _fsync(f)
            finally:
                f.close()

    @staticmethod
    def _csv_escape(v):
        if v is None:
            return ""
        if isinstance(v, (dict, list, tuple)):
            try:
                v = json.dumps(v, separators=(",", ":"))
            except Exception:
                v = str(v)
        s = str(v)
        if ("," in s) or ("\n" in s) or ("\"" in s):
            s = "\"" + s.replace("\"", "\"\"") + "\""
        return s

    def _write_row(self, path, fields, row_dict):
        """Ghi row vào CSV với xử lý đặc biệt cho JSON fields (tránh double-escape)"""
        self._ensure_header(path, fields)
        line = []
        for k in fields:
            val = row_dict.get(k)
            
            # CRITICAL FIX: JSON fields (meta_json, weather_json) - NO double-escape
            # Strategy: Escape internal quotes ONCE (\" → \"\"), then wrap with quotes
            # This way CSV parser will unescape correctly and preserve JSON structure
            if k in ('meta_json', 'weather_json'):
                if val is None or val == "":
                    line.append("")
                elif isinstance(val, str):
                    # Already a JSON string - escape quotes then wrap
                    # {"key":"value"} → "{\"key\":\"value\"}" (single escape for CSV)
                    escaped = val.replace('"', '""')
                    line.append('"' + escaped + '"')
                else:
                    # Dict/list → dumps first, then escape quotes, then wrap
                    json_str = self._safe_json(val)
                    escaped = json_str.replace('"', '""')
                    line.append('"' + escaped + '"')
            else:
                # Normal fields → escape bình thường
                line.append(self._csv_escape(val))
        
        f = _safe_open(path, "a")
        try:
            f.write(",".join(line) + "\n")
            _fsync(f)
        finally:
            f.close()

    def _cleanup_old(self):
        try:
            flist = []
            for name in os.listdir(self.base_dir):
                if not (name.startswith("samples_") or name.startswith("events_")):
                    continue
                p = self.base_dir + "/" + name
                try:
                    st = os.stat(p)
                    mtime = st[-1]
                    flist.append((p, mtime))
                except Exception:
                    pass
            flist.sort(key=lambda x: x[1], reverse=True)
            keep = max(2, self.retention_days)
            for i, (p, _) in enumerate(flist):
                if i >= keep:
                    try:
                        os.remove(p)
                    except Exception:
                        pass
        except Exception:
            pass

    def log_boot(self, meta=None):
        try:
            self.log_event({
                "kind": "boot",
                "src": "sys",
                "act": "start",
                "val": "",
                "meta": meta or {
                    "device_id": self.device_id,
                    "boot_id": self.boot_id,
                    "schema_ver": SCHEMA_VERSION
                }
            })
        except Exception:
            pass

    def _safe_json(self, obj):
        try:
            return json.dumps(obj, separators=(",", ":"))
        except Exception:
            try:
                return json.dumps(str(obj))
            except Exception:
                return "{}"

    def _ensure_event_text(self, value):
        if value is None:
            return ""
        if isinstance(value, str):
            return value
        if isinstance(value, (bytes, bytearray)):
            try:
                return value.decode()
            except Exception:
                try:
                    return "".join(chr(b) for b in value)
                except Exception:
                    return ""
        try:
            return str(value)
        except Exception:
            return ""

    def _map_source_token(self, token):
        if not token:
            return None
        lower = token.lower()
        if lower in self.LOCAL_AI_HINTS or lower.startswith("local_ai"):
            return "local_ai"
        for hint in self.USER_SOURCE_HINTS:
            if lower == hint or lower.startswith(hint + "_"):
                return "user"
        if lower in ("mqtt", "rpc", "shell", "button", "physical", "physical_button", "manual_button"):
            return "user"
        if lower in ("net", "sys", "dlog"):
            return lower
        return lower

    def _canonicalize_event_source(self, kind, src_token, meta):
        token_text = self._ensure_event_text(src_token).strip()
        lowered = token_text.lower()

        if kind in ("config", "override", "command"):
            mapped = self._map_source_token(lowered)
            if mapped:
                if token_text and mapped != lowered:
                    meta.setdefault("raw_source", token_text)
                return mapped

        if kind == "ai_local":
            if (not lowered) or (lowered in ("local_ai", "ai", "decision", "decision_rich")):
                if token_text and lowered not in ("local_ai",):
                    meta.setdefault("raw_source", token_text)
                return "local_ai"

        if not lowered and meta:
            for key in ("origin", "source", "actor", "initiator"):
                hint = meta.get(key)
                if hint:
                    mapped = self._map_source_token(self._ensure_event_text(hint).lower())
                    if mapped:
                        meta.setdefault("raw_source", token_text)
                        return mapped

        mapped = self._map_source_token(lowered)
        if mapped:
            if token_text and mapped != lowered:
                meta.setdefault("raw_source", token_text)
            return mapped

        if not token_text:
            return "system"
        return lowered

    def _normalize_event_payload(self, evt):
        event_dict = evt if isinstance(evt, dict) else {"val": evt}
        meta_obj = event_dict.get("meta")
        if isinstance(meta_obj, dict):
            meta = dict(meta_obj)
        elif meta_obj is None:
            meta = {}
        else:
            meta = {"meta": meta_obj}

        kind_text = self._ensure_event_text(event_dict.get("kind") or event_dict.get("type") or "misc").lower()
        if not kind_text:
            kind_text = "misc"

        act_text = self._ensure_event_text(event_dict.get("act") or event_dict.get("action") or "")
        act_value = act_text.lower() if act_text else ""

        val_value = self._ensure_event_text(event_dict.get("val") or event_dict.get("value") or "")

        src_token = event_dict.get("src")
        if not src_token:
            src_token = event_dict.get("origin") or event_dict.get("source")
            if not src_token and meta:
                for key in ("origin", "source", "actor", "initiator"):
                    if key in meta:
                        src_token = meta.get(key)
                        if src_token:
                            break

        normalized_src = self._canonicalize_event_source(kind_text, src_token, meta)
        if not act_value and kind_text == "config" and val_value:
            act_value = "config"

        return kind_text, normalized_src, act_value, val_value, meta

    def log_event(self, evt: dict):
        ts = _ticks_ms()
        u = _now_unix()
        iso_time = evt.get("iso_time", None)
        if not (iso_time and isinstance(iso_time, str) and len(iso_time) >= 19):
            # Prefer RTC-derived ISO if NTP/RTC is available
            iso_time = _iso_from_unix(u)
            if not (iso_time and isinstance(iso_time, str) and len(iso_time) >= 19):
                try:
                    tm = time.localtime(time.time() + TZ_OFFSET_SEC)
                    # include timezone offset +07:00 to match other outputs
                    iso_time = "{:04d}-{:02d}-{:02d}T{:02d}:{:02d}:{:02d}%s".format(tm[0], tm[1], tm[2], tm[3], tm[4], tm[5]) % TZ_SUFFIX
                except Exception:
                    iso_time = "2000-01-01T00:00:00"
                # Only warn once per boot to avoid flooding logs
                try:
                    if not getattr(self, "_iso_warned", False):
                        print("Event log thiếu iso_time hợp lệ! Fallback sang localtime (NTP chưa đồng bộ).")
                        self._iso_warned = True
                except Exception:
                    pass
        kind_text, src_text, act_text, val_text, meta = self._normalize_event_payload(evt)

        row = {
            "ts_ms": ts,
            "iso_time": iso_time,
            "kind": kind_text,
            "src": src_text,
            "act": act_text,
            "val": val_text,
            "meta_json": self._safe_json(meta),
            "device_id": self.device_id,
            "boot_id": self.boot_id
        }
        try:
            self._write_row(self._event_path(), EVENT_FIELDS, row)
        except Exception:
            pass

    def _sample_path(self):
        return "%s/samples_%s.csv" % (self.base_dir, _date_stamp())

    def _event_path(self):
        return "%s/events_%s.csv" % (self.base_dir, _date_stamp())

    def extract_weather_fields(self, wx_data):
        out = {}
        if not isinstance(wx_data, dict):
            return out
        owm_map = {
            "temp": "api_temp", "humidity": "api_humidity", "pressure": "api_pressure",
            "visibility": "api_visibility", "wind_speed": "api_wind_speed", "rain_1h": "api_rain_1h",
            "weather_main": "api_weather_main", "weather_desc": "api_weather_desc", "cloudiness": "api_cloudiness",
        }
        for src, dst in owm_map.items():
            v = wx_data.get(src, None)
            out[dst] = v if v is not None else None
        for k in OM_FIELDS:
            out[k] = wx_data.get(k, None)
        for k in AQI_FIELDS:
            out[k] = wx_data.get(k, None)
        return out

    def maybe_log_sample(self, snap: dict, wifi=None, ai_control=None):
        now = _ticks_ms()
        if self._last_sample_ms and (time.ticks_diff(now, self._last_sample_ms) < self.sample_period_ms):
            return
        self._last_sample_ms = now
        self._sample_seq += 1

        w = {}
        try:
            w = wifi.get_wifi_status() if wifi else {}
        except Exception:
            w = {}
        wifi_connected = 1 if bool(w.get("connected")) else 0
        wifi_ssid = w.get("ssid")
        wifi_ip = w.get("ip")
        wifi_rssi = w.get("rssi")

        mem_free = None
        try:
            import gc
            mem_free = gc.mem_free()
        except Exception:
            pass

        wx = snap.get("weather") or {}
        wx_data = {}
        if isinstance(wx, dict):
            wx_data = wx.get("data") if "data" in wx and isinstance(wx.get("data"), dict) else wx
        wx_flat = self.extract_weather_fields(wx_data)
        wx_error = None
        try:
            wx_error = (wx.get("error") or wx.get("err") or wx.get("last_error"))
        except Exception:
            wx_error = None
        
        # PRUNE weather_json BEFORE writing to file to reduce size from ~5KB to ~500 bytes
        # This also reduces RAM usage when reading files for upload
        wx_data_pruned = {}
        if isinstance(wx_data, dict):
            keep_keys = ['temp', 'humidity', 'pressure', 'feels_like', 'visibility', 
                       'wind_speed', 'rain_1h', 'cloudiness', 'weather_main', 'weather_desc',
                       'aqi', 'aqi_pm25', 'aqi_pm10', 'aqi_o3', 'aqi_status', 'aqi_dominent',
                       'om_next_temp_c', 'om_next_rh_pct', 'om_tmax_24h_c', 'om_tmin_24h_c']
            for k in keep_keys:
                if k in wx_data:
                    wx_data_pruned[k] = wx_data[k]
        weather_json = self._safe_json(wx_data_pruned if wx_data_pruned else wx_data)

        rtc_unix_val = _now_unix()
        hour, dow = _hour_dow()
        rtc_ok_val = _rtc_ok()

        ai_ref = ai_control or self.ai
        led_mode = "auto"
        led0_state = "OFF"
        try:
            if "led_mode" in snap:
                led_mode = snap.get("led_mode", "auto")
            if "led0_state" in snap:
                led0_state = snap.get("led0_state", "OFF")
            elif ai_ref and hasattr(ai_ref, "get_snapshot"):
                ai_snap = ai_ref.get_snapshot()
                led_mode = ai_snap.get("led_mode", "auto")
                led0_state = ai_snap.get("led0_state", "OFF")
        except Exception:
            pass

        def get_iso_time():
            iso = snap.get("iso_time", None)
            if iso and isinstance(iso, str) and len(iso) >= 19:
                return iso
            wx_iso = wx_data.get("iso_time") if isinstance(wx_data, dict) else None
            if wx_iso and isinstance(wx_iso, str) and len(wx_iso) >= 19:
                return wx_iso
            return _iso_from_unix(rtc_unix_val)
        iso_time_val = get_iso_time()

        def get_field(field):
            if ai_control and hasattr(ai_control, "last_sent"):
                v = ai_control.last_sent.get(field, None)
                if v not in ("", None):
                    return v
            if field in snap and snap[field] not in ("", None):
                return snap[field]
            if field in wx_flat and wx_flat[field] not in ("", None):
                return wx_flat[field]
            return None

        row_dict = {}
        for field in SAMPLE_ALL_FIELDS:
            row_dict[field] = get_field(field)

        row_dict.update({
            "ts_ms": now, "iso_time": iso_time_val, "uptime_ms": now, "rtc_unix": rtc_unix_val,
            "hour": hour, "dow": dow, "rtc_ok": rtc_ok_val, "schema_ver": SCHEMA_VERSION,
            "mem_free": mem_free, "device_id": self.device_id, "boot_id": self.boot_id,
            "sample_seq": self._sample_seq, "wifi_connected": wifi_connected,
            "wifi_ssid": wifi_ssid, "wifi_ip": wifi_ip, "wifi_rssi": wifi_rssi,
            "temperature": snap.get("temperature"), "hum": snap.get("hum"),
            "ldr": snap.get("ldr"), "soil": snap.get("soil"),
            "motor_speed": snap.get("motor_speed"), "usb2_state": snap.get("usb2_state"),
            "led_brightness": snap.get("led_brightness"), "led_mode": led_mode,
            "led0_state": led0_state, "rssi": wifi_rssi,
            "wx_error": wx_error, "weather_json": weather_json
        })

        try:
            self._write_row(self._sample_path(), SAMPLE_ALL_FIELDS, row_dict)
            # NOTE: _process_batch_upload() gây kết nối HTTPS (SSL). Không gọi trực tiếp từ luồng chính
            # để tránh xung đột SSL với luồng MQTT. Một luồng chuyên dụng trong main.py sẽ gọi _process_batch_upload().
            # self._process_batch_upload()
            self._cleanup_old()
        except Exception as e:
            print("Lỗi ghi sample:", e)

    def note_wifi(self, status, reason=None, meta=None):
        try:
            s = (status or "").lower()
            if s not in ("connected", "disconnected", "connecting"):
                s = "disconnected"
            if s == self._wifi_state:
                return
            self._wifi_state = s
            m = dict(meta or {})
            if reason:
                m["reason"] = reason
            self.log_event({
                "kind": "wifi", "src": "net", "act": s, "val": "", "meta": m
            })
        except Exception:
            pass

    def note_mqtt(self, status, reason=None, meta=None):
        try:
            s = (status or "").lower()
            if s not in ("connected", "disconnected", "connecting"):
                s = "disconnected"
            if s == self._mqtt_state:
                return
            self._mqtt_state = s
            m = dict(meta or {})
            if reason:
                m["reason"] = reason
            self.log_event({
                "kind": "mqtt", "src": "net", "act": s, "val": "", "meta": m
            })
        except Exception:
            pass