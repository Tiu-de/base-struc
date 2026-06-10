# config.py - Tập trung toàn bộ cấu hình hệ thống
# Sửa ở đây thay vì lục từng file khi cần thay đổi pin/interval/threshold

# ========== Wi-Fi ==========
WIFI_CONFIG_FILE = "wifi_config.json"  # file lưu ssid/pass sau khi config qua AP
AP_SSID = "ESP32_Config"  # tên AP khi vào chế độ config
CONFIG_PORTAL_TIMEOUT_SEC = 180  # thời gian chờ config AP trước khi bỏ qua (giây)

# ========== MQTT (CoreIoT broker) ==========
MQTT = {
    "SERVER": "app.coreiot.io",
    "PORT": 1883,
    "DEVICE_ID": "ha5yu9i3vihmdyhvsfv5",   # client_id
    "USERNAME": "64hjby860lsgplt40wwg",
    "PASSWORD": "",
    "TOPIC_TELEMETRY": "v1/devices/me/telemetry",       # gửi sensor data
    "TOPIC_ATTRIBUTES": "v1/devices/me/attributes",     # gửi attrs (wifi, boot_id)
    "TOPIC_RPC_REQUEST": "v1/devices/me/rpc/request",   # nhận lệnh RPC
    "TOPIC_RPC_RESPONSE": "v1/devices/me/rpc/response", # trả kết quả RPC
    "TOPIC_COMMANDS": "v1/devices/me/commands",         # nhận lệnh đơn giản (LED, motor, etc)
    "KEEPALIVE": 60,
    "PUBLISH_INTERVAL_MS": 2000,
    "POLL_INTERVAL_MS": 20,
    "PUBLISH_QUEUE_LIMIT": 50,  # giới hạn hàng đợi publish để tránh tràn RAM
}

# ========== OpenWeatherMap (legacy, ít dùng) ==========
OPENWEATHER = {
    "API_KEY": "948151603151927ee11f703d168a102b",
    "CITY": "Thai Nguyen,vn",
    "UNITS": "metric",
    "LANG": "en",
    "TIMEOUT": 10,
    "UPDATE_INTERVAL_MS": 600000,
}

# ========== Weather APIs (Open-Meteo + AQICN) ==========
DEFAULT_LAT = 21.5548   # Thái Nguyên
DEFAULT_LON = 105.8439
DEFAULT_TZ  = "Asia/Bangkok"  # UTC+7
OPEN_METEO_ENABLE = True  # tắt nếu không cần weather forecast
AQICN_ENABLE = True       # tắt nếu không cần AQI
WEATHER_TTL_SEC = 600     # cache weather data 10 phút
HTTP_TIMEOUT_SEC = 8      # timeout các request HTTP (giây)
AQICN_TOKEN = ""          # để trống sẽ dùng token hardcode trong api_control

# ========== LCD 16x2 I2C ==========
LCD = {
    "SCL": 12,
    "SDA": 11,
    "I2C_ADDR": 0x21,  # địa chỉ I2C cố định của LCD hiện tại
    "ROWS": 2,
    "COLS": 16,
    "ADDR_CAND": [0x27, 0x3F, 0x21, 0x20, 0x38],  # list địa chỉ thường gặp để scan
}

# ========== NeoPixel LED ==========
NEOPIXEL = {
    "PIN": 6,                   # GPIO cho dải 4 LED (motor/usb1/usb2/wifi)
    "NUM_LEDS": 4,
    "DEFAULT_BRIGHTNESS": 100,  # 0-255
    "ONBOARD_RGB_PIN": 45       # LED onboard ESP32-S3 (heartbeat)
}

# ========== USB Switch (relay/mosfet) ==========
USB_SWITCH = {
    "CH1_PIN": 10,         # kênh 1 (đèn)
    "CH2_PIN": 17,         # kênh 2 (bơm)
    "INVERT_LOGIC": False, # True nếu relay active-low
}

# ========== Motor (PWM) ==========
MOTOR = {
    "PIN": 18,      # GPIO điều khiển quạt
    "FREQ": 1000,   # tần số PWM (Hz)
}

# ========== Sensors ==========
SENSORS = {
    "DHT_PIN": 8,   # DHT11 nhiệt độ/độ ẩm
    "LDR_PIN": 1,   # ADC ánh sáng
    "SOIL_PIN": 2,  # ADC độ ẩm đất
    "SOIL_DRY": 0,  # giá trị ADC khi đất khô (capacitive: khô = điện dung thấp = ADC thấp)
    "SOIL_WET": 28000,  # giá trị ADC khi đất ướt (capacitive: ướt = điện dung cao = ADC cao)
    "DHT_MIN_INTERVAL_MS": 2000,  # DHT11 cần >= 2s giữa các lần đọc
    "ADC_SAMPLES": 4,  # số mẫu lấy trung bình khi đọc ADC
}

# ========== Vòng lặp chính ==========
LOOPS = {
    "SENSOR_UPDATE_MS": 200,  # đọc sensor mỗi 200ms
    "LCD_UPDATE_MS": 3000,    # cập nhật LCD mỗi 3s
}

SAFE_MODE_PIN = 0  # giữ nút BOOT khi khởi động để vào safe mode
DEBUG = False       # bật log debug (tắt khi chạy production lâu dài)

# ========== Logging / Debug ==========
LOGGING = {
    "ENABLE_DHT_DEBUG": False,  # in log mỗi lần đọc DHT (spam nhiều, chỉ bật khi debug DHT)
    "ENABLE_SENSOR_VERBOSE": False,  # in log khi sensor data thay đổi
    "ENABLE_RAM_LOG": False,  # in RAM còn trống định kỳ (quan trọng để phát hiện leak)
    "RAM_LOG_INTERVAL_MS": 60000,  # log RAM mỗi 1 phút
    "ENABLE_DATA_LOGGER_MEM_DEBUG": False,  # log RAM trước/sau upload (chỉ khi debug upload)
    "ENABLE_API_MEM_DEBUG": False,  # log RAM sau fetch API
    "ENABLE_UPLOAD_THREAD_LOG": False,  # log mỗi lần upload thread chạy
    "ENABLE_UPLOAD_OK_LOG": False,  # in log khi gửi batch lên Google Sheets thành công (HTTP 200)
    "ENABLE_UPLOAD_ERROR_LOG": False,  # in log khi lỗi upload (HTTP error, permanent fail, retry info)
    "ENABLE_LOCAL_AI_DECISION_LOG": False,  # in log mỗi quyết định của Local AI (tắt để giảm spam console)
}

# ========== LED Effects ==========
LED_EFFECTS = {
    "BREATH_MS": 1000,  # chu kỳ nhịp thở (ms)
    "LED_UPDATE_MS": 40,  # cập nhật LED mỗi 40ms (~25fps)
    "MIN_LEVEL": 0.12,  # độ sáng min khi breath (0-1)
    "RAINBOW_BASE_SPEED": 2,  # tốc độ rainbow base
    "RAINBOW_MOTOR_SCALE": 3,  # nhân tốc độ rainbow theo motor (quạt càng nhanh rainbow càng nhanh)
    "LUT_N": 60,  # số điểm trong bảng breath LUT
    "IDLE_HEARTBEAT_MS": 1200,  # chu kỳ heartbeat LED onboard khi idle
    "ONBOARD_DIM_IDLE": 0.35,  # độ tối LED onboard khi idle (0-1)
    "WIFI_CHECK_MS": 1000,  # check wifi mỗi 1s để update LED
    "LED0_OVERRIDE_TIMEOUT_MS": 30000,  # thời gian LED0 giữ màu override (30s)
}

LED_COLORS = {
    "WIFI_OK":   (0, 60, 0),   # xanh lá khi wifi ok
    "WIFI_LOST": (60, 0, 0),   # đỏ khi mất wifi
    "MOTOR":     (0, 20, 0),   # xanh lá nhạt cho motor
    "USB1":      (0, 0, 20),   # xanh dương cho USB1 (đèn)
    "USB2":      (12, 0, 12),  # tím cho USB2 (bơm)
    "OFF":       (0, 0, 0),
}

# offset màu rainbow cho từng LED (độ)
HUE_OFFSETS = {
    "motor": 0,
    "usb1":  85,
    "usb2":  170,
}

# ========== Buttons ==========
BUTTONS = {
    "MOTOR_PIN": 3,  # nút bật/tắt motor
    "USB2_PIN": 4,   # nút bật/tắt USB2 (bơm)
    "DEBOUNCE_MS": 200,  # thời gian chống dội nút (ms)
}

# ========== Data Logger / Upload ==========
DATALOGGER = {
    "BASE_DIR": "logs",  # thư mục chứa file log
    "SAMPLE_PERIOD_MS": 60000,  # ghi sample mỗi 60s (KHÔNG ĐỔI nếu muốn giữ tần suất cũ)
    "RETENTION_DAYS": 7,  # xóa file log cũ hơn 7 ngày
    "GAS_URL_ENDPOINT": "https://script.google.com/macros/s/AKfycbyp5bgyBJ3VRD-54B34CYD1AIOV3vkw1I9Qsz0qg315MmyCWk62H4gYJHbsRHZ0uud1kw/exec",
    
    # === RATE LIMITING (CRITICAL for avoiding HTTP 429) ===
    "BATCH_INTERVAL_MS": 120000,  # quét upload mỗi 120s (tránh rate limit - TĂNG từ 30s)
    "EVENT_MAX_BATCH_SIZE": 15,  # gửi 15 events/batch (TĂNG từ 1 để giảm số requests)
    "SAMPLE_MAX_BATCH_SIZE": 2,  # gửi 2 samples/batch (~3.1 KB, cân bằng với tốc độ ghi 1 sample/phút)
    "UPLOAD_INTER_FILE_DELAY_SEC": 3,  # chờ 2-5s giữa các file (tránh DDoS detection)
    "RETRY_BACKOFF_BASE_SEC": 1,  # exponential backoff base (1s, 2s, 4s, 8s...)
    "RETRY_JITTER_PERCENT": 20,  # jitter ±20% để tránh thundering herd
    "RATE_LIMIT_BACKOFF_SEC": 60,  # backoff mặc định khi gặp HTTP 429 (60s)
    "RATE_LIMIT_MAX_BACKOFF_SEC": 600,  # max backoff 10 phút
    
    # === PAYLOAD LIMITS ===
    "PROCESS_LINES_PER_FILE": 3,  # xử lý tối đa 3 dòng/file mỗi lần quét (tránh lag)
    "MAX_FIELD_LEN": 6144,  # giới hạn độ dài trường CSV (bytes)
    "BATCH_PAYLOAD_BYTES_LIMIT": 20 * 1024,  # giới hạn payload trước khi buộc gửi (20KB)
    "MAX_PAYLOAD": 8000,  # giới hạn payload JSON khi gửi trực tiếp
    "HTTP_POST_TIMEOUT_SEC": 20,  # timeout POST (giây)
}

# chu kỳ upload thread check file mới (giây)
UPLOAD_THREAD_INTERVAL_SEC = 60

# ========== AI Control / MQTT Telemetry ==========
AI_CONTROL = {
    "CORE_TELE_INTERVAL_MS": 500,  # gửi sensor data mỗi 500ms
    "ATTR_INTERVAL_MS": 5000,  # gửi attributes mỗi 5s
    "WX_TELE_ENABLE": True,  # bật gửi weather qua MQTT
    "WX_TELE_INTERVAL_MS": 15000,  # gửi weather mỗi 15s
    "WX_TELE_JITTER_MS": 300,  # jitter ngẫu nhiên để tránh đồng bộ với core tele
    "WEATHER_COMPACT_MAX_BYTES": 900,  # giới hạn kích thước weather packet
    "MQTT_RETRY_MS": 5000,  # retry kết nối MQTT sau 5s nếu lỗi
    "PING_INTERVAL_MS": 30000,  # ping MQTT mỗi 30s
    "MAX_SENDS_PER_LOOP": 8,  # giới hạn số message gửi mỗi loop (tránh flood)
    "MANUAL_OVERRIDE_MS": 5 * 60 * 1000,  # thời gian manual override (5 phút)
}
