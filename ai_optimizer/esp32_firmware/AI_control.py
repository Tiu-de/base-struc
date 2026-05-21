# ====================== AI_control.py (V4.2 - Chunked Update) ======================
import time, _thread, gc, machine, uos
try:
    from umqtt.simple import MQTTClient
    import ujson
    import ubinascii  # For base64
    import uhashlib  # For SHA256
except ImportError as e:
    print("Lỗi import trong AI_control:", e)
    raise

# ------- Data Logger (dùng chung từ main) -------
# Để main.py truyền vào qua register hoặc setter, tránh tạo duplicate logger
dlog_ai = None

# ========= MQTT CoreIoT (from config) =========
try:
    import config as CFG
    _MQTT = CFG.MQTT if hasattr(CFG, "MQTT") else {}
    MQTT_SERVER    = _MQTT.get("SERVER", "app.coreiot.io")
    MQTT_PORT      = int(_MQTT.get("PORT", 1883))
    MQTT_DEVICE_ID = _MQTT.get("DEVICE_ID", "ha5yu9i3vihmdyhvsfv5")
    MQTT_USERNAME  = _MQTT.get("USERNAME", "64hjby860lsgplt40wwg")
    MQTT_PASSWORD  = _MQTT.get("PASSWORD", "")
    MQTT_TOPIC_TELEMETRY = _MQTT.get("TOPIC_TELEMETRY", "v1/devices/me/telemetry")
    MQTT_TOPIC_ATTRS     = _MQTT.get("TOPIC_ATTRIBUTES", "v1/devices/me/attributes")
    MQTT_TOPIC_RPC_REQ   = _MQTT.get("TOPIC_RPC_REQUEST", "v1/devices/me/rpc/request")
    MQTT_TOPIC_RPC_RESP  = _MQTT.get("TOPIC_RPC_RESPONSE", "v1/devices/me/rpc/response")
    MQTT_TOPIC_COMMANDS  = _MQTT.get("TOPIC_COMMANDS", "v1/devices/me/commands")
except Exception:
    MQTT_SERVER = "app.coreiot.io"
    MQTT_PORT = 1883
    MQTT_DEVICE_ID = "ha5yu9i3vihmdyhvsfv5"
    MQTT_USERNAME = "64hjby860lsgplt40wwg"
    MQTT_PASSWORD = ""
    MQTT_TOPIC_TELEMETRY = "v1/devices/me/telemetry"
    MQTT_TOPIC_ATTRS = "v1/devices/me/attributes"
    MQTT_TOPIC_RPC_REQ = "v1/devices/me/rpc/request"
    MQTT_TOPIC_RPC_RESP = "v1/devices/me/rpc/response"
    MQTT_TOPIC_COMMANDS = "v1/devices/me/commands"

# Bytes topics
MQTT_TOPIC_TELEMETRY_B = MQTT_TOPIC_TELEMETRY.encode()
MQTT_TOPIC_ATTRS_B     = MQTT_TOPIC_ATTRS.encode()
MQTT_TOPIC_RPC_REQ_B   = (MQTT_TOPIC_RPC_REQ + "/+").encode()
MQTT_TOPIC_RPC_RESP_B  = MQTT_TOPIC_RPC_RESP.encode()
# MỚI: commands wildcard subscribe
MQTT_TOPIC_COMMANDS_B  = (MQTT_TOPIC_COMMANDS + "/+").encode()

# ========= Intervals / Limits (from config) =========
try:
    _AI = CFG.AI_CONTROL if hasattr(CFG, "AI_CONTROL") else {}
    CORE_TELE_INTERVAL_MS = int(_AI.get("CORE_TELE_INTERVAL_MS", 500))
    ATTR_INTERVAL_MS = int(_AI.get("ATTR_INTERVAL_MS", 5000))
    WX_TELE_ENABLE = bool(_AI.get("WX_TELE_ENABLE", True))
    WX_TELE_INTERVAL_MS = int(_AI.get("WX_TELE_INTERVAL_MS", 15000))
    WX_TELE_JITTER_MS = int(_AI.get("WX_TELE_JITTER_MS", 300))
    WEATHER_COMPACT_MAX_BYTES = int(_AI.get("WEATHER_COMPACT_MAX_BYTES", 900))
    MQTT_RETRY_MS = int(_AI.get("MQTT_RETRY_MS", 5000))
    PING_INTERVAL_MS = int(_AI.get("PING_INTERVAL_MS", 30000))
    MAX_SENDS_PER_LOOP = int(_AI.get("MAX_SENDS_PER_LOOP", 8))
    MANUAL_OVERRIDE_MS = int(_AI.get("MANUAL_OVERRIDE_MS", 5 * 60 * 1000))
except Exception:
    CORE_TELE_INTERVAL_MS = 500
    ATTR_INTERVAL_MS = 5000
    WX_TELE_ENABLE = True
    WX_TELE_INTERVAL_MS = 15000
    WX_TELE_JITTER_MS = 300
    WEATHER_COMPACT_MAX_BYTES = 900
    MQTT_RETRY_MS = 5000
    PING_INTERVAL_MS = 30000
    MAX_SENDS_PER_LOOP = 8
    MANUAL_OVERRIDE_MS = 5 * 60 * 1000

# ========= Logging flags =========
LOG_ENABLE      = False
LOG_MQTT        = False
LOG_CORE_PUB    = False
LOG_WX_PUB      = False
LOG_PUB_ATTR    = False
LOG_QUEUE       = False
LOG_RPC         = False
LOG_VERBOSE_ERR = False
LOG_HEALTH      = False

BENIGN_LOG_EVERY_MS = 2000

def _ts_ms():
    try:
        return time.ticks_ms()
    except Exception:
        return int(time.time()*1000)

def _log(can, tag, *args):
    if LOG_ENABLE and can:
        print("[{:d}] [{}]".format(_ts_ms(), tag), *args)

# ========= Command queue (AI → MAIN) =========
class AICommandQueue:
    def __init__(self, maxlen=32):
        self.q = []
        self.lock = _thread.allocate_lock()
        self.maxlen = maxlen

    def push(self, cmd: dict):
        with self.lock:
            if len(self.q) >= self.maxlen:
                self.q.pop(0)
            self.q.append(cmd)

    def pop(self):
        with self.lock:
            if not self.q:
                return None
            return self.q.pop(0)

    def clear(self):
        with self.lock:
            self.q.clear()

    # Mới: hỗ trợ len(queue) an toàn từ thread khác
    def __len__(self):
        with self.lock:
            return len(self.q)

# ========= SimpleQueue dùng cho RPC-in =========
class SimpleQueue:
    def __init__(self, maxlen=32):
        self.q = []
        self.lock = _thread.allocate_lock()
        self.maxlen = maxlen

    def push(self, item):
        with self.lock:
            if len(self.q) >= self.maxlen:
                self.q.pop(0)
            self.q.append(item)

    def pop(self):
        with self.lock:
            if not self.q:
                return None
            return self.q.pop(0)

    # Mới: cho phép len(simple_queue) an toàn
    def __len__(self):
        with self.lock:
            return len(self.q)

# ========= Publish queue (mọi publish đi qua đây) =========
class PublishQueue:
    """
    Coalesce theo khóa (ck) cho gói lặp (CORE/WX/ATTR) => chỉ giữ bản mới nhất.
    RPC (ck=None) => giữ nguyên thứ tự/đầy đủ.
    """
    def __init__(self, maxlen=128):
        self.q = []
        self.lock = _thread.allocate_lock()
        self.maxlen = maxlen

    def push(self, topic_b, payload_b, tag="UNK", ck=None):
        if isinstance(payload_b, str):
            payload_b = payload_b.encode()
        with self.lock:
            if ck is not None:
                for i in range(len(self.q)-1, -1, -1):
                    if self.q[i]["ck"] == ck:
                        self.q.pop(i)
                        _log(LOG_QUEUE, "Q", "Coalesce drop ck=", ck)
            if len(self.q) >= self.maxlen:
                dropped = self.q.pop(0)
                _log(LOG_QUEUE, "Q", "Drop oldest tag=", dropped["tag"], "len=", len(self.q))
            self.q.append({"topic": topic_b, "payload": payload_b, "tag": tag, "ck": ck})
            if LOG_QUEUE:
                _log(True, "Q", "ENQ tag=", tag, "ck=", ck, "len=", len(self.q), "bytes=", len(payload_b))

    def pop(self):
        with self.lock:
            if not self.q:
                return None
            itm = self.q.pop(0)
            if LOG_QUEUE:
                _log(True, "Q", "DEQ tag=", itm["tag"], "ck=", itm["ck"], "len=", len(self.q), "bytes=", len(itm["payload"]))
            return itm

    def push_front(self, item):
        with self.lock:
            self.q.insert(0, item)
            if len(self.q) > self.maxlen:
                self.q.pop()
            if LOG_QUEUE:
                _log(True, "Q", "RE-ENQ FRONT tag=", item["tag"], "ck=", item["ck"], "len=", len(self.q))

    def __len__(self):
        with self.lock:
            return len(self.q)

# ========= AIControl =========
class AIControl:
    def __init__(self, wifi,
                 temperature_ref=None, hum_ref=None, ldr_ref=None, soil_ref=None,
                 motor_speed_ref=0, usb1_state_ref='OFF', usb2_state_ref='OFF',
                 led_brightness_ref=100, weather_data_ref=None, leds=None, onboard_led=None):  # <-- thêm onboard_led param
        self.wwifi = wifi

        # Snapshot chung
        self.snap_lock = _thread.allocate_lock()
        self.snapshot = {
            "temperature": temperature_ref,
            "hum": hum_ref,
            "ldr": ldr_ref,
            "soil": soil_ref,
            "motor_speed": motor_speed_ref or 0,
            "usb1_state": usb1_state_ref or 'OFF',
            "usb2_state": usb2_state_ref or 'OFF',
            "led_brightness": led_brightness_ref if led_brightness_ref is not None else 100,
            "led_mode": "auto",  # <-- ĐÃ THÊM: TRẠNG THÁI "led_mode"
            "rssi": None,
            "weather": weather_data_ref if weather_data_ref else {"status": False, "data": {}},
            "profile_update_status": "idle",
            "profile_update_pct": 0,
        }

        # MQTT runtime
        self.mqtt = None
        self.mqtt_ok = False
        self._mqtt_lock = _thread.allocate_lock()  # chỉ dùng khi connect/disconnect
        self.mqtt_last_retry = time.ticks_ms()
        self._last_ping = time.ticks_ms()
        self._last_benign_log = 0

        # Schedulers
        now = time.ticks_ms()
        self._core_tele_due = now
        self._core_interval = CORE_TELE_INTERVAL_MS
        self._wx_due        = time.ticks_add(now, WX_TELE_JITTER_MS)
        self._attr_due      = time.ticks_add(now, 1000)  # sẽ ENQ ATTR ngay khi connect

        # Queues
        self.cmd_queue = AICommandQueue(maxlen=32)
        self.pub_q     = PublishQueue(maxlen=128)
        self.rpc_in_q  = SimpleQueue(maxlen=32)     # HÀNG ĐỢI RPC VÀO (tách khỏi callback)
        
        # === THÊM MỚI: THAM CHIẾU ĐẾN LOCAL_AI (CHO HOT-RELOAD) ===
        self.local_ai = None 
        # === KẾT THÚC THÊM MỚI ===

        # Threads
        try:
            _thread.start_new_thread(self._mqtt_thread, ())
            _thread.start_new_thread(self._scheduler_thread, ())
            _thread.start_new_thread(self._rpc_handler_thread, ())   # <— MỚI
        except Exception as e:
            print("AIControl thread start err:", e)

        print("AIControl khởi tạo thành công (keys: temperature, hum, ldr, soil)")

        self.last_sent = {}  # Lưu snapshot cuối cùng đã gửi lên MQTT

        # Tham chiếu dẫn tới controller NeoPixel (nếu truyền)
        self.leds = leds
        # tham chiếu tới on-board LED controller (nếu có)
        self.onboard_led = onboard_led

        # Manual override runtime
        self._manual_lock = _thread.allocate_lock()
        # per-action overrides: key -> ticks_until
        # keys: None/"global", "led0", "usb2", "usb1", "motor", ...
        self._manual_overrides = {}

        # === NEW: Chunked Profile Update State ===
        self.update_state = {
            "is_active": False,
            "file_path": None,
            "tmp_path": None,
            "expected_size": 0,
            "expected_hash": None,
            "bytes_written": 0,
            "file_handle": None
        }
        # === END NEW ===

    # ====== Public API cho MAIN ======
    def set_snapshot(self, snap: dict, immediate=False):
        with self.snap_lock:
            for k, v in snap.items():
                self.snapshot[k] = v
        if immediate:
            now = time.ticks_ms()
            self._core_tele_due = now
            self._attr_due      = now
            self._wx_due        = now
            _log(LOG_MQTT, "CORE", "Snapshot updated (immediate).")
            _log(LOG_MQTT, "WX",   "Snapshot updated (immediate).")

    def on_local_change(self):
        now = time.ticks_ms()
        self._core_tele_due = now
        self._attr_due      = now
        _log(LOG_MQTT, "CORE", "Local change -> schedule immediate publish.")

    def pop_command(self):
        return self.cmd_queue.pop()

    # <--- [THÊM MỚI]
    def push_local_command(self, cmd_dict):
        """
        [HÀM MỚI] Cho phép AI cục bộ (hoặc luồng khác) đẩy lệnh vào hàng đợi.
        """
        try:
            # self.cmd_queue là một AICommandQueue,
            # nó có hàm .push() (định nghĩa ở dòng 96)
            self.cmd_queue.push(cmd_dict) 
            return True
        except Exception as e:
            print(f"AIControl Lỗi push_local_command: {e}")
            return False

    # NEW: an toàn lấy snapshot hiện thời (copy) cho các client đọc
    def get_snapshot(self):
        """
        Trả về bản sao dict của snapshot hiện thời (thread-safe).
        Dùng để LocalAI hoặc các thành phần khác kiểm tra trạng thái trước khi gửi lệnh.
        """
        try:
            with self.snap_lock:
                return dict(self.snapshot)
        except Exception:
            return {}

    # Manual override API (per-action)
    def set_manual_override(self, duration_ms=None, action=None, source=None):
        """Set manual override. action: None or str (e.g. 'led0','usb2'). duration_ms default MANUAL_OVERRIDE_MS."""
        if duration_ms is None:
            duration_ms = MANUAL_OVERRIDE_MS
        key = "global" if (action is None or action == "") else str(action)
        try:
            with self._manual_lock:
                self._manual_overrides[key] = time.ticks_add(time.ticks_ms(), int(duration_ms))
            origin = "local_ai"
            if source is not None:
                try:
                    origin = str(source)
                except Exception:
                    origin = "local_ai"
            origin_norm = origin.lower()
            user_tokens = ("user", "mqtt", "rpc", "shell", "physical", "physical_button", "button", "manual")
            actor = "user" if (origin_norm.startswith("user") or origin_norm in user_tokens) else "local_ai"
            # log event
            try:
                if dlog_ai:
                    dlog_ai.log_event({
                        "kind": "override",
                        "src": actor,
                        "act": key,  # Action name (usb2, motor, global, etc)
                        "val": "ACTIVE",  # Override state
                        "meta": {"duration_ms": int(duration_ms), "origin": origin_norm}
                    })
            except Exception:
                pass
            _log(LOG_MQTT, "MANUAL", "set", key, "for", duration_ms)
            return True
        except Exception:
            return False
        
    def clear_manual_override(self, action=None, source=None):
        """Clear manual override for given action or global when action is None."""
        key = "global" if (action is None or action == "") else str(action)
        try:
            with self._manual_lock:
                if key in self._manual_overrides:
                    del self._manual_overrides[key]
            origin = None
            if source is not None:
                try:
                    origin = str(source)
                except Exception:
                    origin = None
            origin_norm = origin.lower() if origin else ""
            user_tokens = ("user", "mqtt", "rpc", "shell", "physical", "physical_button", "button", "manual")
            actor = "user" if (origin_norm and (origin_norm.startswith("user") or origin_norm in user_tokens)) else "local_ai"
            try:
                if dlog_ai:
                    dlog_ai.log_event({
                        "kind": "override",
                        "src": actor,
                        "act": key,  # Action name being cleared
                        "val": "CLEARED",  # State change
                        "meta": ({"origin": origin_norm} if origin_norm else {})
                    })
            except Exception:
                pass
            _log(LOG_MQTT, "MANUAL", "cleared", key)
            return True
        except Exception:
            return False

    def is_manual_override_active(self, action=None):
        """Return True if global override active or specific action override active."""
        try:
            now = time.ticks_ms()
            with self._manual_lock:
                # global check
                g = self._manual_overrides.get("global")
                if g and time.ticks_diff(g, now) > 0:
                    return True
                if action is None:
                    return False
                k = str(action)
                u = self._manual_overrides.get(k)
                if u and time.ticks_diff(u, now) > 0:
                    return True
            return False
        except Exception:
            return False

    # === THÊM MỚI: HÀM ĐĂNG KÝ (CHO HOT-RELOAD) ===
    def register_local_ai(self, local_ai_instance):
        """
        Nhận tham chiếu đến local_ai từ main.py
        """
        self.local_ai = local_ai_instance
    # === KẾT THÚC THÊM MỚI ===

    # ====== MQTT helpers ======
    def _disconnect_silent(self, reason=""):
        try:
            with self._mqtt_lock:
                if self.mqtt:
                    try:
                        self.mqtt.disconnect()
                    except Exception:
                        pass
                self.mqtt = None
                self.mqtt_ok = False
            _log(LOG_MQTT, "MQTT", "Disconnected.", reason)
            # ghi vào event log (thêm)
            try:
                if dlog_ai:
                    dlog_ai.note_mqtt("disconnected", reason=reason)
            except Exception:
                pass
        except Exception:
            self.mqtt = None
            self.mqtt_ok = False

    def _set_sock_timeouts(self):
        """Thiết lập timeout để tránh publish treo (tùy build usocket)."""
        try:
            with self._mqtt_lock:
                m = self.mqtt
            if not m:
                return
            s = getattr(m, "sock", None)
            if s:
                try:
                    s.settimeout(2)  # 2s
                    _log(LOG_MQTT, "MQTT", "Socket timeout set.")
                except Exception as e:
                    _log(LOG_MQTT, "MQTT", "Socket settimeout not supported:", e)
        except Exception as e:
            _log(LOG_MQTT, "MQTT", "Socket timeout setup err:", e)

    def _init_mqtt(self):
        try:
            st = self.wwifi.get_wifi_status() if self.wwifi else {}
            if not st.get("connected"):
                _log(LOG_MQTT, "MQTT", "Wi-Fi chưa kết nối, hoãn MQTT.")
                return False

            self._disconnect_silent("before reconnect")

            with self._mqtt_lock:
                m = MQTTClient(
                    client_id=MQTT_DEVICE_ID,
                    server=MQTT_SERVER,
                    port=MQTT_PORT,
                    user=MQTT_USERNAME,
                    password=MQTT_PASSWORD,
                    keepalive=60
                )
                m.set_callback(self._on_rpc)  # callback giờ chỉ đẩy vào rpc_in_q / attr handler
                m.connect()
                # RPC topic (existing)
                m.subscribe(MQTT_TOPIC_RPC_REQ_B)
                # subscribe attributes updates
                try:
                    m.subscribe(MQTT_TOPIC_ATTRS_B)
                    m.subscribe(MQTT_TOPIC_ATTRS_B + b"/+")
                except Exception:
                    pass
                # MỚI: subscribe commands topic so cloud can simply PUBLISH to control device
                try:
                    m.subscribe(MQTT_TOPIC_COMMANDS_B)
                except Exception:
                    pass

                self.mqtt = m
                self.mqtt_ok = True
                self._last_ping = time.ticks_ms()

            self._set_sock_timeouts()
            _log(LOG_MQTT, "MQTT", "Kết nối & SUB RPC/ATTR OK")

            # enqueue ATTR ngay lúc connect
            self._queue_attributes()

            # log connected (thêm)
            try:
                if dlog_ai:
                    dlog_ai.note_mqtt("connected", meta={"broker": "%s:%d" % (MQTT_SERVER, MQTT_PORT)})
            except Exception:
                pass

            return True

        except Exception as e:
            if LOG_VERBOSE_ERR:
                try:
                    etype = str(type(e))
                    _log(True, "MQTT", "init err:", e, "type:", etype)
                except Exception:
                    print("MQTT init err:", e)
            else:
                print("MQTT init err:", e)
            self._disconnect_silent("init failed")
            return False

    def _is_benign_oserr(self, e):
        try:
            code = e.args[0]
        except Exception:
            code = None
        return code in (-1, 11)  # -1 generic / 11 EAGAIN

    def _is_timeout_oserr(self, e):
        try:
            code = e.args[0]
        except Exception:
            code = None
        return code in (-110,)

    def _ping_if_needed(self):
        if not self.mqtt_ok:
            return
        now = time.ticks_ms()
        if time.ticks_diff(now, self._last_ping) >= PING_INTERVAL_MS:
            try:
                with self._mqtt_lock:
                    m = self.mqtt
                if m:
                    m.ping()
                self._last_ping = now
                _log(LOG_MQTT, "MQTT", "PING")
            except Exception as e:
                _log(LOG_MQTT, "MQTT", "Ping error:", e)
                self._disconnect_silent("ping failed")

    # ====== MQTT thread (sole socket owner) ======
    def _mqtt_thread(self):
        while True:
            try:
                if not self.mqtt_ok:
                    now = time.ticks_ms()
                    if time.ticks_diff(now, self.mqtt_last_retry) >= MQTT_RETRY_MS:
                        _log(LOG_MQTT, "MQTT", "Thử kết nối MQTT...")
                        # log connecting (thêm)
                        try:
                            if dlog_ai:
                                dlog_ai.note_mqtt("connecting", reason="dial")
                        except Exception:
                            pass
                        self._init_mqtt()
                        self.mqtt_last_retry = now
                    time.sleep(0.05); machine.idle()
                    continue

                # receive RPC → callback (_on_rpc) sẽ chỉ đẩy vào rpc_in_q
                try:
                    with self._mqtt_lock:
                        m = self.mqtt
                    if m:
                        m.check_msg()
                except OSError as e:
                    if self._is_benign_oserr(e):
                        now = time.ticks_ms()
                        if time.ticks_diff(now, self._last_benign_log) >= BENIGN_LOG_EVERY_MS:
                            _log(LOG_MQTT, "MQTT", "check_msg benign:", e)
                            self._last_benign_log = now
                    else:
                        if LOG_VERBOSE_ERR:
                            try:
                                etype = str(type(e))
                                _log(True, "MQTT", "check_msg err:", e, "type:", etype)
                            except Exception:
                                print("MQTT check_msg err:", e)
                        else:
                            print("MQTT check_msg err:", e)
                        if LOG_HEALTH:
                            try:
                                st = self.wwifi.get_wifi_status() if self.wwifi else {}
                                rssi = st.get("rssi", "N/A")
                            except Exception:
                                rssi = "N/A"
                            _log(LOG_HEALTH, "MQTT", "Health: RSSI=", rssi, "MemFree=", gc.mem_free())
                        self._disconnect_silent("check_msg failed")
                        continue

                # ping
                self._ping_if_needed()

                # publish burst
                sends = 0
                while sends < MAX_SENDS_PER_LOOP:
                    item = self.pub_q.pop()
                    if not item:
                        break
                    try:
                        with self._mqtt_lock:
                            m = self.mqtt
                        if not (self.mqtt_ok and m):
                            self.pub_q.push_front(item)
                            break
                        m.publish(item["topic"], item["payload"])
                        sends += 1
                    except OSError as e:
                        self.pub_q.push_front(item)
                        if self._is_timeout_oserr(e):
                            _log(LOG_MQTT, "MQTT", "Publish timeout OSError:", e)
                        if LOG_HEALTH:
                            try:
                                st = self.wwifi.get_wifi_status() if self.wwifi else {}
                                rssi = st.get("rssi", "N/A")
                            except Exception:
                                rssi = "N/A"
                            _log(LOG_HEALTH, "MQTT", "Publish err; RSSI=", rssi, "MemFree=", gc.mem_free())
                        self._disconnect_silent("publish failed")
                        break
                    except Exception as e:
                        self.pub_q.push_front(item)
                        if LOG_VERBOSE_ERR:
                            _log(True, "MQTT", "publish exception:", e)
                        # log disconnected do ngoại lệ publish (thêm)
                        try:
                            if dlog_ai:
                                dlog_ai.note_mqtt("disconnected", reason="publish_exception", meta={"stage": "publish"})
                        except Exception:
                            pass
                        self._disconnect_silent("publish exception")
                        break

                time.sleep_ms(15)
                machine.idle()
                gc.collect()

            except Exception as e:
                print("mqtt_thread warn:", e)
                time.sleep(0.1)

    # ====== RPC utils ======
    @staticmethod
    def _to_bool_onoff(val):
        try:
            if isinstance(val, bool): return val
            if isinstance(val, (int, float)): return bool(int(val))
            if isinstance(val, (bytes, bytearray)):
                val = val.decode()
            if isinstance(val, str):
                s = val.strip().lower()
                if s in ("1", "on", "true", "yes", "y"): return True
                if s in ("0", "off", "false", "no", "n", ""): return False
        except Exception:
            pass
        return bool(val)

    @staticmethod
    def _norm_method_name(method_raw):
        if not method_raw:
            return ""
        try:
            if isinstance(method_raw, (bytes, bytearray)):
                method_raw = method_raw.decode()
        except Exception:
            pass
        s = str(method_raw).lower()
        out = []
        for ch in s:
            if "a" <= ch <= "z" or "0" <= ch <= "9":
                out.append(ch)
        return "".join(out)

    # MỚI: parse RGB từ params cho RPC setLed0
    def _parse_rgb(self, params):
        """Parse RGB params giống main._parse_rgb_val — trả về (r,g,b) hoặc None."""
        try:
            p = params
            if isinstance(p, (bytes, bytearray)):
                p = p.decode()
            if isinstance(p, (list, tuple)) and len(p) >= 3:
                return (int(p[0]) & 255, int(p[1]) & 255, int(p[2]) & 255)
            if isinstance(p, dict):
                r = int(p.get("r", p.get("R", 0) or 0))
                g = int(p.get("g", p.get("G", 0) or 0))
                b = int(p.get("b", p.get("B", 0) or 0))
                return (r & 255, g & 255, b & 255)
            if isinstance(p, str):
                s = p.strip()
                if s.startswith("#") and len(s) >= 7:
                    try:
                        r = int(s[1:3], 16); g = int(s[3:5], 16); b = int(s[5:7], 16)
                        return (r, g, b)
                    except Exception:
                        pass
                parts = [q.strip() for q in s.split(",") if q.strip()!=""]
                if len(parts) >= 3:
                    return (int(parts[0]) & 255, int(parts[1]) & 255, int(parts[2]) & 255)
            if isinstance(p, (int, float)):
                v = int(p) & 255
                return (v, v, v)
        except Exception:
            pass
        return None

    # MỚI: xử lý attribute update từ broker (ví dụ: server set attributes)
    def _handle_attr_update(self, topic_str, msg):
        """
        Parse attribute update payload and enqueue local commands.
        Supports payload forms:
          - {"shared": {"led0": [r,g,b], ...}}
          - {"led0": [r,g,b], ...}
          - {"led0": "#RRGGBB"} etc.
        """
        try:
            payload = None
            try:
                payload = ujson.loads(msg)
            except Exception:
                # không phải json -> ignore
                return
            # extract attrs dict: prefer "shared" key if present
            if isinstance(payload, dict) and "shared" in payload and isinstance(payload["shared"], dict):
                attrs = payload["shared"]
            elif isinstance(payload, dict):
                attrs = payload
            else:
                return

            # handle specific keys
            if "led0" in attrs:
                raw = attrs.get("led0")
                rgb = self._parse_rgb(raw)
                if rgb is None:
                    # allow false/null to turn off
                    if raw in (None, False, 0, "0", "off", "OFF", ""):
                        cmd = {"type": "led0", "value": [0,0,0]}
                        self.cmd_queue.push(cmd)
                        # update snapshot & schedule attr publish
                        try:
                            # <-- SỬA: "KHÓA" AI KHI CÓ LỆNH TỪ ATTRIBUTES
                            self.set_snapshot({"led0": [0,0,0], "led_mode": "manual"}, immediate=False)
                            self.on_local_change()
                        except Exception:
                            pass
                    else:
                        # invalid value -> ignore
                        pass
                else:
                    cmd = {"type": "led0", "value": [int(rgb[0]), int(rgb[1]), int(rgb[2])]}
                    self.cmd_queue.push(cmd)
                    # update snapshot & schedule attr publish
                    try:
                        # <-- SỬA: "KHÓA" AI KHI CÓ LỆNH TỪ ATTRIBUTES
                        self.set_snapshot({"led0": [int(rgb[0]), int(rgb[1]), int(rgb[2])], "led_mode": "manual"}, immediate=False)
                        self.on_local_change()
                    except Exception:
                        pass
                    # <-- SỬA: XÓA GHI ĐÈ CŨ
                    # (Không cần gọi self.set_manual_override)

            # potential: handle other attributes (e.g., led_brightness) similarly
            elif "led_brightness" in attrs:
                try:
                    b = attrs.get("led_brightness")
                    if isinstance(b, (int, float, str, bool)):
                        vb = int(float(b)) if not isinstance(b, bool) else (100 if b else 0)
                        vb = max(0, min(100, vb))
                        cmd = {"type": "led_brightness", "value": vb}
                        self.cmd_queue.push(cmd)
                        try:
                            # <-- SỬA: "KHÓA" AI KHI THAY ĐỔI BRIGHTNESS
                            self.set_snapshot({"led_brightness": vb, "led_mode": "manual"}, immediate=False)
                            self.on_local_change()
                        except Exception:
                            pass
                except Exception:
                    pass
            
            # === THÊM MỚI: CHUYỂN PROFILE ĐANG HOẠT ĐỘNG BẰNG THUỘC TÍNH ===
            elif "active_profile" in attrs:
                try:
                    new_name = attrs.get("active_profile")
                    if isinstance(new_name, (bytes, bytearray)):
                        new_name = new_name.decode()
                    if isinstance(new_name, str) and len(new_name.strip()) > 0:
                        new_name = new_name.strip()
                        try:
                            with open("plant_profiles.json", "r") as f:
                                data = ujson.loads(f.read())
                        except Exception:
                            data = None

                        if isinstance(data, dict):
                            profiles = data.get("profiles", {}) if isinstance(data.get("profiles"), dict) else {}
                            if new_name in profiles:
                                data["active_profile"] = new_name
                                try:
                                    with open("plant_profiles.json", "w") as f:
                                        f.write(ujson.dumps(data))
                                    _log(True, "PROFILE", "Set active_profile via ATTR:", new_name)
                                except Exception as e:
                                    _log(True, "PROFILE", "Write profile err:", e)
                                # Hot-reload
                                if hasattr(self, "local_ai") and self.local_ai and hasattr(self.local_ai, "reload_profile"):
                                    try:
                                        self.local_ai.reload_profile()
                                    except Exception:
                                        pass
                            else:
                                _log(True, "PROFILE", "active_profile not found:", new_name)
                except Exception as e:
                    _log(True, "PROFILE", f"Attr active_profile err: {e}")
            
            # === THÊM MỚI: HỖ TRỢ HOT-RELOAD PROFILE ===
            elif "plant_profile_config" in attrs:
                try:
                    new_profile_content = attrs.get("plant_profile_config")
                    
                    if new_profile_content and isinstance(new_profile_content, (str, bytes)):
                        # 1. Lưu file mới đè lên file cũ
                        with open("plant_profiles.json", "w") as f:
                            f.write(new_profile_content)
                        
                        _log(True, "PROFILE", "Da luu plant_profiles.json tu Cloud!")
                        
                        # 2. Gọi Hot-Reload thay vì Reset
                        if hasattr(self, "local_ai") and self.local_ai and hasattr(self.local_ai, "reload_profile"):
                            self.local_ai.reload_profile()
                        else:
                            # Fallback: Nếu lỗi, vẫn reset
                            _log(True, "PROFILE", "Loi Hot-Reload, dang Reset...")
                            time.sleep(2)
                            machine.reset()
                            
                except Exception as e:
                    _log(True, "PROFILE", f"Loi cap nhat profile: {e}")
            # === KẾT THÚC THÊM MỚI ===

        except Exception as e:
            _log(LOG_RPC, "ATTR", "handle_attr_update err:", e)

    def _handle_command_topic(self, topic_str, msg):
        """
        Handle simple command publishes on topic 'v1/devices/me/commands/+'.
        Accept payload forms:
          - {"led0": [r,g,b]} or {"led0":"#RRGGBB"}  -> treated as setLed0
          - {"cmd":"setLed0","value": ...}
          - {"cmd":"led0","value": ...}
        Converts to internal cmd_queue item: {"type":"led0","value": ...}
        Also updates snapshot and schedules attribute publish.
        """
        try:
            data = None
            try:
                data = ujson.loads(msg)
            except Exception:
                # not JSON -> ignore
                return
            if not isinstance(data, dict):
                return

            # helper: publish ack back to commands/<tag>/ack if available
            def _publish_cmd_ack(tag, status, payload_obj):
                try:
                    ack_topic = (MQTT_TOPIC_COMMANDS + "/" + str(tag) + "/ack").encode()
                    ack_payload = ujson.dumps({"status": status, "payload": payload_obj})
                    self.pub_q.push(ack_topic, ack_payload, tag="CMD_ACK", ck=None)
                except Exception:
                    pass

            # get tag from incoming topic (last token)
            try:
                tag = topic_str.split("/")[-1] if topic_str else "unknown"
            except Exception:
                tag = "unknown"

            # Direct shorthand: {"led0": ...}
            if "led0" in data:
                raw = data.get("led0")
                rgb = self._parse_rgb(raw)
                if rgb is None:
                    if raw in (None, False, 0, "0", "off", "OFF", ""):
                        cmd = {"type": "led0", "value": [0,0,0]}
                        self.cmd_queue.push(cmd)
                        try:
                            # <-- SỬA: "KHÓA" AI KHI CÓ LỆNH TỪ COMMANDS
                            self.set_snapshot({"led0": [0,0,0], "led_mode": "manual"}, immediate=False); self.on_local_change()
                        except Exception:
                            pass
                        _publish_cmd_ack(tag, "queued", {"type":"led0","value":[0,0,0]})
                    return
                cmd = {"type": "led0", "value": [int(rgb[0]), int(rgb[1]), int(rgb[2])]}
                self.cmd_queue.push(cmd)
                try:
                    # <-- SỬA: "KHÓA" AI KHI CÓ LỆNH TỪ COMMANDS
                    self.set_snapshot({"led0": [int(rgb[0]), int(rgb[1]), int(rgb[2])], "led_mode": "manual"}, immediate=False); self.on_local_change()
                except Exception:
                    pass
                # <-- SỬA: XÓA GHI ĐÈ CŨ
                # (Không cần gọi self.set_manual_override)
                _publish_cmd_ack(tag, "queued", {"type":"led0","value":cmd["value"]})
                return

            # Generic command envelope
            cmd_name = data.get("cmd") or data.get("method")
            if not cmd_name:
                return
            mkey = self._norm_method_name(cmd_name)
            if mkey in ("setled0", "led0"):
                raw = data.get("value") or data.get("params")
                rgb = self._parse_rgb(raw)
                if rgb is None:
                    if raw in (None, False, 0, "0", "off", "OFF", ""):
                        cmd = {"type": "led0", "value": [0,0,0]}
                        self.cmd_queue.push(cmd)
                        try:
                            # <-- SỬA: "KHÓA" AI KHI CÓ LỆNH TỪ COMMANDS
                            self.set_snapshot({"led0": [0,0,0], "led_mode": "manual"}, immediate=False); self.on_local_change()
                        except Exception:
                            pass
                        _publish_cmd_ack(tag, "queued", {"type":"led0","value":[0,0,0]})
                    return
                cmd = {"type": "led0", "value": [int(rgb[0]), int(rgb[1]), int(rgb[2])]}
                self.cmd_queue.push(cmd)
                try:
                    # <-- SỬA: "KHÓA" AI KHI CÓ LỆNH TỪ COMMANDS
                    self.set_snapshot({"led0": [int(rgb[0]), int(rgb[1]), int(rgb[2])], "led_mode": "manual"}, immediate=False); self.on_local_change()
                except Exception:
                    pass
                # <-- SỬA: XÓA GHI ĐÈ CŨ
                # (Không cần gọi self.set_manual_override)
                _publish_cmd_ack(tag, "queued", {"type":"led0","value":cmd["value"]})
                return

        except Exception as e:
            _log(LOG_RPC, "CMD", "handle_command_topic err:", e)

    # ====== MQTT callback: NHẸ – chỉ parse & đẩy vào rpc_in_q or attr handler ======
    def _on_rpc(self, topic, msg):
        try:
            # topic có thể là bytes
            t = topic.decode() if isinstance(topic, (bytes, bytearray)) else topic

            # Nếu message từ topic attributes -> xử lý attribute update
            try:
                if "attributes" in t:
                    self._handle_attr_update(t, msg)
                    return
            except Exception:
                pass

            # Nếu message từ topic commands -> xử lý lệnh đơn giản
            try:
                if "commands" in t:
                    self._handle_command_topic(t, msg)
                    return
            except Exception:
                pass

            parts = t.split('/')
            req_id = parts[-1] if len(parts) >= 6 else None

            # parse tối thiểu rồi thoát (RPC)
            data = ujson.loads(msg)
            method_raw = data.get("method")
            params = data["params"] if ("params" in data) else data.get("value", None)

            item = {"req_id": req_id, "method_raw": method_raw, "params": params}
            self.rpc_in_q.push(item)

            if LOG_RPC:
                _log(LOG_RPC, "RPC", "In(CB→Q):", item)

        except Exception as e:
            # nếu parse lỗi, vẫn báo về server (qua publish queue)
            try:
                err_payload = ujson.dumps({"status": "error", "message": "rpc parse: " + str(e)})
                self.pub_q.push(MQTT_TOPIC_RPC_RESP_B, err_payload, tag="RPC", ck=None)
            except Exception:
                pass
            if LOG_VERBOSE_ERR:
                _log(True, "RPC", "callback err:", e)

    # ====== RPC handler thread: xử lý thực sự & trả lời ======
    def _rpc_handler_thread(self):
        # Rebuilt handler (previous version got truncated). Includes chunked update RPCs.
        while True:
            try:
                job = self.rpc_in_q.pop()
                if not job:
                    time.sleep(0.01)
                    continue

                req_id     = job.get("req_id")
                method_raw = job.get("method_raw")
                params     = job.get("params")
                if params is None:
                    params = {}
                mkey       = self._norm_method_name(method_raw)

                if LOG_RPC:
                    _log(LOG_RPC, "RPC", "Handle:", {"req": req_id, "mkey": mkey, "params": params})

                with self.snap_lock:
                    snap = dict(self.snapshot)

                response = {}
                cmd = None

                # --- BASIC GET/SET ---
                if mkey == "getmotorspeed":
                    response = {"motor_speed": int(snap.get("motor_speed") or 0)}

                elif mkey == "setmotorspeed":
                    if isinstance(params, dict):
                        for key in ("value", "speed", "motor_speed", "level"):
                            if key in params:
                                params = params.get(key)
                                break
                    if isinstance(params, (int, float, bool, str)):
                        try:
                            val = int(float(params)) if not isinstance(params, bool) else (100 if params else 0)
                        except Exception:
                            val = 0
                        val = max(0, min(100, int(val)))
                        cmd = {"type": "motor_speed", "value": val}
                        response = {"status": "queued", "motor_speed": val}
                    else:
                        response = {"status": "error", "message": "invalid speed"}

                elif mkey == "getledbrightness":
                    response = {"led_brightness": int(snap.get("led_brightness") or 0)}

                elif mkey == "setledbrightness":
                    if isinstance(params, (int, float, bool, str)):
                        try:
                            val = int(float(params)) if not isinstance(params, bool) else (100 if params else 0)
                        except Exception:
                            val = 0
                        val = max(0, min(100, int(val)))
                        cmd = {"type": "led_brightness", "value": val}
                        self.set_snapshot({"led_mode": "manual"}, immediate=False)
                        response = {"status": "queued", "led_brightness": val}
                    else:
                        response = {"status": "error", "message": "invalid brightness"}

                elif mkey == "setusb1":
                    state = self._to_bool_onoff(params)
                    cmd = {"type": "usb1", "value": 1 if state else 0}
                    response = {"status": "queued", "usb1": "ON" if state else "OFF"}
                    self._core_tele_due = time.ticks_ms()

                elif mkey == "setusb2":
                    state = self._to_bool_onoff(params)
                    cmd = {"type": "usb2", "value": 1 if state else 0}
                    response = {"status": "queued", "usb2": "ON" if state else "OFF"}
                    self._core_tele_due = time.ticks_ms()

                elif mkey == "getusb1state":
                    response = {"usb1_state": snap.get("usb1_state")}

                elif mkey == "getusb2state":
                    response = {"usb2_state": snap.get("usb2_state")}

                elif mkey == "getstatus":
                    response = {
                        "motor_speed": int(snap.get("motor_speed") or 0),
                        "usb1_state": snap.get("usb1_state"),
                        "usb2_state": snap.get("usb2_state"),
                        "led_brightness": int(snap.get("led_brightness") or 0)
                    }

                elif mkey == "setled0":
                    rgb = self._parse_rgb(params)
                    if rgb is None:
                        response = {"status": "error", "message": "invalid color"}
                    else:
                        cmd = {"type": "led0", "value": [int(rgb[0]), int(rgb[1]), int(rgb[2])]}
                        self.set_snapshot({"led_mode": "manual"}, immediate=False)
                        response = {"status": "queued", "led0": [rgb[0], rgb[1], rgb[2]]}

                elif mkey == "getled0":
                    try:
                        target = None
                        if hasattr(self, "leds") and self.leds is not None:
                            target = self.leds
                        elif hasattr(self, "onboard_led") and self.onboard_led is not None:
                            target = self.onboard_led
                        if target and hasattr(target, "get_led_state"):
                            st = target.get_led_state(0)
                            if st and st.get("status"):
                                response = {"led0": {"r": int(st.get("r",0)), "g": int(st.get("g",0)), "b": int(st.get("b",0)), "brightness": int(st.get("brightness",0))}}
                            else:
                                response = {"status": "error", "message": "led_read_failed"}
                        else:
                            response = {"status": "error", "message": "no_led_interface"}
                    except Exception as e:
                        response = {"status": "error", "message": "exception: %s" % str(e)}

                elif mkey == "setledmode":
                    mode_val = str(params).lower().strip()
                    if mode_val in ("auto", "manual"):
                        self.set_snapshot({"led_mode": mode_val}, immediate=True)
                        response = {"status": "ok", "led_mode": mode_val}
                        try:
                            if dlog_ai:
                                dlog_ai.log_event({
                                    "kind": "config",
                                    "src": "user",
                                    "act": "set_led_mode",
                                    "val": mode_val,
                                    "meta": {"origin": "mqtt"}
                                })
                        except Exception:
                            pass
                    else:
                        response = {"status": "error", "message": "mode must be 'auto' or 'manual'"}

                elif mkey == "getledmode":
                    response = {"led_mode": snap.get("led_mode", "auto")}

                # --- PROFILE HOT-RELOAD (legacy full file) ---
                elif mkey in ("setprofile", "updateplantprofiles"):
                    try:
                        new_profile_content = params
                        if new_profile_content and isinstance(new_profile_content, (str, bytes)):
                            try:
                                with open("plant_profiles.json", "r") as f_old:
                                    old_content = f_old.read()
                                with open("plant_profiles.json.bak", "w") as f_bak:
                                    f_bak.write(old_content)
                            except Exception:
                                pass
                            with open("plant_profiles.json", "w") as f:
                                f.write(new_profile_content)
                            response = {"status": "ok", "message": "profile saved, reloading"}
                            if req_id:
                                self.pub_q.push(MQTT_TOPIC_RPC_RESP_B + b"/" + req_id.encode(), ujson.dumps(response), tag="RPC", ck=None)
                            if self.local_ai and hasattr(self.local_ai, "reload_profile"):
                                self.local_ai.reload_profile()
                            else:
                                time.sleep(2); machine.reset()
                            continue  # already responded
                        else:
                            response = {"status": "error", "message": "invalid profile content"}
                    except Exception as e:
                        response = {"status": "error", "message": str(e)}

                elif mkey in ("setactiveprofile", "switchprofile"):
                    try:
                        prof = params
                        reset_cycle = False  # Default: giữ nguyên plant_start_date
                        
                        # Parse parameters
                        if isinstance(prof, dict):
                            reset_cycle = bool(prof.get("reset_cycle", False))
                            prof = prof.get("name") or prof.get("profile")
                        if isinstance(prof, (bytes, bytearray)):
                            prof = prof.decode()
                        if not isinstance(prof, str) or len(prof.strip()) == 0:
                            response = {"status": "error", "message": "profile name required"}
                        else:
                            prof = prof.strip()
                            try:
                                with open("plant_profiles.json", "r") as f:
                                    data = ujson.loads(f.read())
                            except Exception:
                                data = None
                            if not isinstance(data, dict):
                                response = {"status": "error", "message": "profile file read error"}
                            else:
                                profiles = data.get("profiles", {}) if isinstance(data.get("profiles"), dict) else {}
                                if prof not in profiles:
                                    response = {"status": "error", "message": "profile not found", "requested": prof}
                                else:
                                    data["active_profile"] = prof
                                    try:
                                        with open("plant_profiles.json", "w") as f:
                                            f.write(ujson.dumps(data))
                                        
                                        # Update growth state
                                        if self.local_ai and hasattr(self.local_ai, "growth_state"):
                                            self.local_ai.growth_state.update_profile(prof, reset_cycle=reset_cycle)
                                        
                                        ack = {"status": "ok", "active_profile": prof, "reset_cycle": reset_cycle}
                                        if req_id:
                                            self.pub_q.push(MQTT_TOPIC_RPC_RESP_B + b"/" + req_id.encode(), ujson.dumps(ack), tag="RPC", ck=None)
                                        if self.local_ai and hasattr(self.local_ai, "reload_profile"):
                                            self.local_ai.reload_profile()
                                        else:
                                            time.sleep(2); machine.reset()
                                        continue  # already responded
                                    except Exception as e:
                                        response = {"status": "error", "message": "write failed: %s" % str(e)}
                    except Exception as e:
                        response = {"status": "error", "message": str(e)}

                elif mkey == "getactiveprofile":
                    try:
                        with open("plant_profiles.json", "r") as f:
                            data = ujson.loads(f.read())
                        ap = data.get("active_profile")
                        response = {"active_profile": ap}
                    except Exception as e:
                        response = {"status": "error", "message": str(e)}

                elif mkey == "listprofiles":
                    try:
                        with open("plant_profiles.json", "r") as f:
                            data = ujson.loads(f.read())
                        profiles = data.get("profiles", {})
                        profs = list(profiles.keys()) if isinstance(profiles, dict) else []
                        response = {"profiles": profs, "active_profile": data.get("active_profile")}
                    except Exception as e:
                        response = {"status": "error", "message": str(e)}

                # --- PLANT LIFECYCLE MANAGEMENT ---
                elif mkey == "resetplantcycle":
                    # Reset chu kỳ trồng cây (khi trồng cây mới hoàn toàn)
                    try:
                        new_start = params.get("start_date") if isinstance(params, dict) else None
                        if self.local_ai and hasattr(self.local_ai, "growth_state"):
                            self.local_ai.growth_state.reset_plant_cycle(new_start)
                            summary = self.local_ai.growth_state.get_state_summary()
                            response = {"status": "ok", "message": "plant_cycle_reset", "state": summary}
                        else:
                            response = {"status": "error", "message": "growth_state_not_available"}
                    except Exception as e:
                        response = {"status": "error", "message": str(e)}

                elif mkey == "setplantdate":
                    # Override plant_start_date (để sửa lỗi hoặc điều chỉnh thủ công)
                    try:
                        timestamp = params.get("timestamp") if isinstance(params, dict) else params
                        if not isinstance(timestamp, (int, float)):
                            response = {"status": "error", "message": "timestamp_required"}
                        elif self.local_ai and hasattr(self.local_ai, "growth_state"):
                            self.local_ai.growth_state.set_plant_start_date(int(timestamp))
                            summary = self.local_ai.growth_state.get_state_summary()
                            response = {"status": "ok", "message": "plant_date_updated", "state": summary}
                        else:
                            response = {"status": "error", "message": "growth_state_not_available"}
                    except Exception as e:
                        response = {"status": "error", "message": str(e)}

                elif mkey == "getgrowthstate":
                    # Lấy trạng thái chu kỳ phát triển hiện tại
                    try:
                        if self.local_ai and hasattr(self.local_ai, "growth_state"):
                            summary = self.local_ai.growth_state.get_state_summary()
                            response = {"status": "ok", "state": summary}
                        else:
                            response = {"status": "error", "message": "growth_state_not_available"}
                    except Exception as e:
                        response = {"status": "error", "message": str(e)}

                # --- CHUNKED UPDATE (V2) ---
                elif mkey == "startprofileupdate":
                    try:
                        file_size = int(params.get("file_size"))
                        sha256_hex = params.get("sha256")
                        if self.update_state["is_active"]:
                            response = {"status": "error", "message": "update_already_in_progress"}
                        elif not all([isinstance(file_size, int), file_size > 0, isinstance(sha256_hex, str), len(sha256_hex) == 64]):
                            response = {"status": "error", "message": "invalid_params"}
                        else:
                            if self.local_ai: self.local_ai.start_update()
                            try:
                                uos.remove("plant_profiles.json.tmp")
                            except OSError:
                                pass
                            fh = open("plant_profiles.json.tmp", "wb")
                            self.update_state.update({
                                "is_active": True,
                                "tmp_path": "plant_profiles.json.tmp",
                                "expected_size": file_size,
                                "expected_hash": sha256_hex,
                                "bytes_written": 0,
                                "file_handle": fh
                            })
                            self.set_snapshot({
                                "profile_update_status": "in_progress",
                                "profile_update_pct": 0
                            }, immediate=True)
                            response = {"status": "ok", "message": "ready_for_chunks"}
                            _log(True, "RPC_UPDATE", "Start update size=", file_size)
                    except Exception as e:
                        response = {"status": "error", "message": str(e)}

                elif mkey == "appendprofilechunk":
                    if not self.update_state["is_active"]:
                        response = {"status": "error", "message": "no_update_in_progress"}
                    else:
                        try:
                            offset = int(params.get("offset"))
                            chunk_b64 = params.get("chunk")
                            if offset != self.update_state["bytes_written"]:
                                response = {"status": "error", "message": "incorrect_offset", "expected": self.update_state["bytes_written"]}
                            else:
                                chunk_bytes = ubinascii.a2b_base64(chunk_b64)
                                fh = self.update_state["file_handle"]
                                fh.write(chunk_bytes)
                                self.update_state["bytes_written"] += len(chunk_bytes)
                                pct = int((self.update_state["bytes_written"] * 100) / self.update_state["expected_size"])
                                self.set_snapshot({
                                    "profile_update_pct": pct,
                                    "profile_update_status": "in_progress"
                                }, immediate=False)
                                response = {"status": "ok", "bytes_written": self.update_state["bytes_written"], "pct": pct}
                        except Exception as e:
                            response = {"status": "error", "message": str(e)}

                elif mkey == "commitprofileupdate":
                    if not self.update_state["is_active"]:
                        response = {"status": "error", "message": "no_update_in_progress"}
                    else:
                        try:
                            fh = self.update_state["file_handle"]
                            if fh:
                                fh.close()
                                self.update_state["file_handle"] = None
                            final_size = uos.stat(self.update_state["tmp_path"])[6]
                            if final_size != self.update_state["expected_size"]:
                                raise ValueError("size_mismatch")
                            h = uhashlib.sha256()
                            with open(self.update_state["tmp_path"], "rb") as fchk:
                                while True:
                                    buf = fchk.read(256)
                                    if not buf:
                                        break
                                    h.update(buf)
                            calc = ubinascii.hexlify(h.digest()).decode()
                            if calc != self.update_state["expected_hash"]:
                                raise ValueError("hash_mismatch")
                            uos.rename(self.update_state["tmp_path"], "plant_profiles.json")
                            response = {"status": "ok", "message": "commit_successful", "hash": calc}
                            _log(True, "RPC_UPDATE", "Commit OK")
                            if self.local_ai: self.local_ai.reload_profile()
                            self.set_snapshot({
                                "profile_update_status": "success",
                                "profile_update_pct": 100
                            }, immediate=True)
                        except Exception as e:
                            try:
                                uos.remove(self.update_state.get("tmp_path") or "plant_profiles.json.tmp")
                            except OSError:
                                pass
                            response = {"status": "error", "message": "commit_failed: %s" % str(e)}
                            self.set_snapshot({
                                "profile_update_status": "error",
                                "profile_update_pct": 0
                            }, immediate=True)
                        finally:
                            if self.local_ai: self.local_ai.end_update(success=(response.get("status") == "ok"))
                            self.update_state = {"is_active": False, "file_path": None, "tmp_path": None, "expected_size": 0, "expected_hash": None, "bytes_written": 0, "file_handle": None}

                elif mkey == "abortprofileupdate":
                    if not self.update_state["is_active"]:
                        response = {"status": "error", "message": "no_update_in_progress"}
                    else:
                        try:
                            fh = self.update_state.get("file_handle")
                            if fh:
                                fh.close()
                            try:
                                uos.remove(self.update_state.get("tmp_path") or "plant_profiles.json.tmp")
                            except OSError:
                                pass
                            response = {"status": "ok", "message": "aborted"}
                        except Exception as e:
                            response = {"status": "error", "message": str(e)}
                        finally:
                            if self.local_ai: self.local_ai.end_update(success=False)
                            self.set_snapshot({
                                "profile_update_status": "aborted",
                                "profile_update_pct": 0
                            }, immediate=True)
                            self.update_state = {"is_active": False, "file_path": None, "tmp_path": None, "expected_size": 0, "expected_hash": None, "bytes_written": 0, "file_handle": None}

                # --- DEFAULT FALLBACK ---
                else:
                    response = {"status": "error", "message": "unknown_method", "method": mkey}

                # Enqueue command if any
                if cmd:
                    self.cmd_queue.push(cmd)
                    try:
                        t = cmd.get("type")
                        if t == "usb2":
                            self.set_manual_override(action="usb2", source="user_mqtt")
                        elif t == "usb1":
                            self.set_manual_override(action="usb1", source="user_mqtt")
                        elif t == "motor_speed":
                            self.set_manual_override(action="motor", source="user_mqtt")
                    except Exception:
                        pass

                if req_id:
                    try:
                        self.pub_q.push(MQTT_TOPIC_RPC_RESP_B + b"/" + req_id.encode(), ujson.dumps(response), tag="RPC", ck=None)
                    except Exception:
                        pass
                if LOG_RPC:
                    _log(LOG_RPC, "RPC", "Out:", {"req": req_id, "resp": response})

            except Exception as e:
                print("rpc_handler warn:", e)
                time.sleep(0.05)

    # ---------- Weather compact ----------
    @staticmethod
    def _make_weather_compact(weather_obj, max_bytes):
        out = {}
        if not isinstance(weather_obj, dict):
            return out
        data = weather_obj["data"] if ("data" in weather_obj and isinstance(weather_obj.get("data"), dict)) else weather_obj
        priority = [
            ("api_temp",         data.get("temp")),
            ("api_humidity",     data.get("humidity")),
            ("api_weather",      data.get("weather_main") or data.get("weather_desc")),
            ("weather_temp",     data.get("temp")),
            ("weather_humidity", data.get("humidity")),
            ("weather_pressure", data.get("pressure")),
            ("weather_wind_speed",data.get("wind_speed")),
            ("weather_visibility",data.get("visibility")),
            ("weather_main",     data.get("weather_main") or data.get("weather_desc")),
            ("weather_rain_1h",  data.get("rain_1h", 0)),
        ]
        def try_add(key, val):
            if val is None:
                return True
            out[key] = val
            try:
                s = ujson.dumps(out)
                if len(s) > max_bytes:
                    out.pop(key, None)
                    return False
                return True
            except Exception:
                out.pop(key, None)
                return True
        for k, v in priority:
            if not try_add(k, v): return out
        return out

    def _queue_core(self):
        with self.snap_lock:
            s = self.snapshot
            wifi = self.wwifi.get_wifi_status() if self.wwifi else {}
            data = {
                "temperature": s.get("temperature", "N/A"),
                "hum":         s.get("hum", "N/A"),
                "ldr":         s.get("ldr", "N/A"),
                "soil":        s.get("soil", "N/A"),
                "motor_speed": s.get("motor_speed", 0),
                "usb1_state":  s.get("usb1_state", "OFF"),
                "usb2_state":  s.get("usb2_state", "OFF"),
                "led_brightness": s.get("led_brightness", 100),
                "rssi": wifi.get("rssi", "N/A"),
            }
        payload = ujson.dumps(data)
        self.pub_q.push(MQTT_TOPIC_TELEMETRY_B, payload, tag="CORE", ck="CORE")
        for k, v in data.items():
            if self.last_sent.get(k) != v:
                self.last_sent[k] = v

    def _queue_wx(self):
        with self.snap_lock:
            w = self.snapshot.get("weather")
            data = self._make_weather_compact(w, WEATHER_COMPACT_MAX_BYTES)
        if not data:
            return
        payload = ujson.dumps(data)
        self.pub_q.push(MQTT_TOPIC_TELEMETRY_B, payload, tag="WX", ck="WX")
        for k, v in data.items():
            if self.last_sent.get(k) != v:
                self.last_sent[k] = v

    def _make_attrs(self):
        with self.snap_lock:
            s = self.snapshot
            led0_v = s.get("led0", None)
            led0_list = None
            try:
                if isinstance(led0_v, (list, tuple)) and len(led0_v) >= 3:
                    led0_list = [int(led0_v[0]) & 255, int(led0_v[1]) & 255, int(led0_v[2]) & 255]
            except Exception:
                led0_list = None
            try:
                led0_state = "OFF"
                if isinstance(led0_list, (list, tuple)) and any((int(x) & 255) > 0 for x in led0_list):
                    led0_state = "ON"
            except Exception:
                led0_state = "OFF"
            active_prof = None
            try:
                with open("plant_profiles.json", "r") as f:
                    data = ujson.loads(f.read())
                if isinstance(data, dict):
                    ap = data.get("active_profile")
                    if isinstance(ap, str): active_prof = ap
            except Exception:
                active_prof = None
            attrs = {
                "motor_speed": int(s.get("motor_speed") or 0),
                "usb1_state":  s.get("usb1_state", "OFF"),
                "usb2_state":  s.get("usb2_state", "OFF"),
                "led_brightness": int(s.get("led_brightness") or 0),
                "led0": led0_list,
                "led0_state": led0_state,
                "led_mode": s.get("led_mode", "auto"),
                "active_profile": active_prof,
                "profile_update_pct": s.get("profile_update_pct", 0),
                "profile_update_status": s.get("profile_update_status", "idle")
            }
            return attrs

    def _queue_attributes(self):
        attrs = self._make_attrs()
        payload = ujson.dumps(attrs)
        self.pub_q.push(MQTT_TOPIC_ATTRS_B, payload, tag="ATTR", ck="ATTR")
        for k, v in attrs.items():
            if self.last_sent.get(k) != v:
                self.last_sent[k] = v

    def _scheduler_thread(self):
        while True:
            try:
                now = time.ticks_ms()
                if time.ticks_diff(now, self._core_tele_due) >= 0:
                    self._queue_core(); self._core_tele_due = time.ticks_add(now, self._core_interval)
                if time.ticks_diff(now, self._attr_due) >= 0:
                    self._queue_attributes(); self._attr_due = time.ticks_add(now, ATTR_INTERVAL_MS)
                if WX_TELE_ENABLE and time.ticks_diff(now, self._wx_due) >= 0:
                    self._queue_wx(); self._wx_due = time.ticks_add(now, WX_TELE_INTERVAL_MS)
                time.sleep(0.03); machine.idle(); gc.collect()
            except Exception as e:
                print("scheduler_thread warn:", e); time.sleep(0.2)