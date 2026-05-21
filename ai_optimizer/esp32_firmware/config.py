# config.py - Tập trung toàn bộ cấu hình hệ thống
# Sửa ở đây thay vì lục từng file khi cần thay đổi pin/interval/threshold

# ========== Wi-Fi ==========
WIFI_CONFIG_FILE = "wifi_config.json"
AP_SSID = "ESP32_Config"
CONFIG_PORTAL_TIMEOUT_SEC = 120

# ========== MQTT (CoreIoT broker) ==========
MQTT = {
    "SERVER": "app.coreiot.io",
    "PORT": 1883,
    "DEVICE_ID": "",      # Điền DEVICE_ID của bạn
    "USERNAME": "",       # Điền USERNAME
    "PASSWORD": "",
    "TOPIC_TELEMETRY": "v1/devices/me/telemetry",
    "TOPIC_ATTRIBUTES": "v1/devices/me/attributes",
    "TOPIC_RPC_REQUEST": "v1/devices/me/rpc/request",
    "TOPIC_RPC_RESPONSE": "v1/devices/me/rpc/response",
    "TOPIC_COMMANDS": "v1/devices/me/commands",
    "KEEPALIVE": 60,
    "PUBLISH_INTERVAL_MS": 2000,
    "POLL_INTERVAL_MS": 20,
    "PUBLISH_QUEUE_LIMIT": 50,
}

# ========== Weather APIs ==========
DEFAULT_LAT = 21.5548
DEFAULT_LON = 105.8439
DEFAULT_TZ  = "Asia/Bangkok"
OPEN_METEO_ENABLE = True
AQICN_ENABLE = True
WEATHER_TTL_SEC = 600
HTTP_TIMEOUT_SEC = 8
AQICN_TOKEN = ""  # Điền AQICN token của bạn

# ========== LCD 16x2 I2C ==========
LCD = {
    "SCL": 12,
    "SDA": 11,
    "I2C_ADDR": 0x21,
    "ROWS": 2,
    "COLS": 16,
    "ADDR_CAND": [0x27, 0x3F, 0x21, 0x20, 0x38],
}

# ========== NeoPixel LED ==========
NEOPIXEL = {
    "PIN": 6,
    "NUM_LEDS": 4,
    "DEFAULT_BRIGHTNESS": 100,
    "ONBOARD_RGB_PIN": 45
}

# ========== USB Switch (relay/mosfet) ==========
USB_SWITCH = {
    "CH1_PIN": 10,
    "CH2_PIN": 17,
    "INVERT_LOGIC": False,
}

# ========== Motor (PWM) ==========
MOTOR = {
    "PIN": 18,
    "FREQ": 1000,
}

# ========== Sensors ==========
SENSORS = {
    "DHT_PIN": 8,
    "LDR_PIN": 1,
    "SOIL_PIN": 2,
    "SOIL_DRY": 0,
    "SOIL_WET": 28000,
    "DHT_MIN_INTERVAL_MS": 2000,
    "ADC_SAMPLES": 4,
}

# ========== Vòng lặp chính ==========
LOOPS = {
    "SENSOR_UPDATE_MS": 200,
    "LCD_UPDATE_MS": 3000,
}

SAFE_MODE_PIN = 0
DEBUG = False

# ========== Logging ==========
LOGGING = {
    "ENABLE_DHT_DEBUG": False,
    "ENABLE_SENSOR_VERBOSE": False,
    "ENABLE_RAM_LOG": False,
    "RAM_LOG_INTERVAL_MS": 60000,
    "ENABLE_DATA_LOGGER_MEM_DEBUG": False,
    "ENABLE_API_MEM_DEBUG": False,
    "ENABLE_UPLOAD_THREAD_LOG": False,
    "ENABLE_UPLOAD_OK_LOG": False,
    "ENABLE_UPLOAD_ERROR_LOG": False,
    "ENABLE_LOCAL_AI_DECISION_LOG": False,
}

# ========== LED Effects ==========
LED_EFFECTS = {
    "BREATH_MS": 1000,
    "LED_UPDATE_MS": 40,
    "MIN_LEVEL": 0.12,
    "RAINBOW_BASE_SPEED": 2,
    "RAINBOW_MOTOR_SCALE": 3,
    "LUT_N": 60,
    "IDLE_HEARTBEAT_MS": 1200,
    "ONBOARD_DIM_IDLE": 0.35,
    "WIFI_CHECK_MS": 1000,
    "LED0_OVERRIDE_TIMEOUT_MS": 30000,
}

LED_COLORS = {
    "WIFI_OK":   (0, 60, 0),
    "WIFI_LOST": (60, 0, 0),
    "MOTOR":     (0, 20, 0),
    "USB1":      (0, 0, 20),
    "USB2":      (12, 0, 12),
    "OFF":       (0, 0, 0),
}

HUE_OFFSETS = {
    "motor": 0,
    "usb1":  85,
    "usb2":  170,
}

# ========== Buttons ==========
BUTTONS = {
    "MOTOR_PIN": 3,
    "USB2_PIN": 4,
    "DEBOUNCE_MS": 200,
}

# ========== Data Logger / Upload ==========
DATALOGGER = {
    "BASE_DIR": "logs",
    "SAMPLE_PERIOD_MS": 60000,
    "RETENTION_DAYS": 7,
    "GAS_URL_ENDPOINT": "",  # Điền URL Google Apps Script của bạn
    "BATCH_INTERVAL_MS": 120000,
    "EVENT_MAX_BATCH_SIZE": 15,
    "SAMPLE_MAX_BATCH_SIZE": 2,
    "UPLOAD_INTER_FILE_DELAY_SEC": 3,
    "RETRY_BACKOFF_BASE_SEC": 1,
    "RETRY_JITTER_PERCENT": 20,
    "RATE_LIMIT_BACKOFF_SEC": 60,
    "RATE_LIMIT_MAX_BACKOFF_SEC": 600,
    "PROCESS_LINES_PER_FILE": 3,
    "MAX_FIELD_LEN": 6144,
    "BATCH_PAYLOAD_BYTES_LIMIT": 20 * 1024,
    "MAX_PAYLOAD": 8000,
    "HTTP_POST_TIMEOUT_SEC": 20,
}

UPLOAD_THREAD_INTERVAL_SEC = 60

# ========== AI Control ==========
AI_CONTROL = {
    "CORE_TELE_INTERVAL_MS": 500,
    "ATTR_INTERVAL_MS": 5000,
    "WX_TELE_ENABLE": True,
    "WX_TELE_INTERVAL_MS": 15000,
    "WX_TELE_JITTER_MS": 300,
    "WEATHER_COMPACT_MAX_BYTES": 900,
    "MQTT_RETRY_MS": 5000,
    "PING_INTERVAL_MS": 30000,
    "MAX_SENDS_PER_LOOP": 8,
    "MANUAL_OVERRIDE_MS": 5 * 60 * 1000,
}
