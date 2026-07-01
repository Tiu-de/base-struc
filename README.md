# ESP32-S3 Smart Farm System - Hướng dẫn

## Tổng quan hệ thống

Hệ thống tự động chăm sóc cây trồng với:
- Đọc sensor (DHT11, ánh sáng, độ ẩm đất)
- Điều khiển motor (quạt), USB switch (đèn + bơm)
- Upload dữ liệu lên Google Sheets
- MQTT telemetry/RPC qua CoreIoT
- LCD hiển thị status
- LED effects (breath, rainbow)
- Local AI logic tự động (dựa trên plant profiles)

## Cấu trúc file

### Core files (QUAN TRỌNG - đừng xóa)

- **config.py** - Tập trung TẤT CẢ cấu hình (pin, interval, threshold)
  - Sửa ở đây thay vì lục từng file
  - Đổi GPIO: sửa `SENSORS`, `MOTOR`, `USB_SWITCH`, `NEOPIXEL`, `LCD`
  - Đổi interval: sửa `LOOPS`, `AI_CONTROL`, `DATALOGGER`
  - Đổi threshold: sửa `SENSORS.SOIL_DRY/WET`
  
- **main.py** - Orchestrator chính, khởi chạy tất cả module + threads
  - Đừng chạy trực tiếp, để boot.py tự gọi
  - Có 10+ threads: LED, button, LCD watchdog, wifi watchdog, upload, MQTT, scheduler, etc
  
- **boot.py** - Chạy đầu tiên khi ESP32 boot (hiện tại để trống)

### Sensor & Actuator

- **sensor_control.py** - Đọc DHT11, LDR, Soil moisture
  - DHT11 có retry logic + cache (sensor hay lỗi timeout)
  - ADC đọc trung bình nhiều mẫu (config.SENSORS.ADC_SAMPLES)
  - Soil calibration: SOIL_DRY=1000 (0% - khô), SOIL_WET=4000 (100% - ướt)
  
- **motor_control.py** - Điều khiển quạt PWM (0-100%)
  - GPIO 18, freq 1000Hz (config.MOTOR)
  
- **usb_switch_controller.py** - Điều khiển 2 kênh relay/MOSFET
  - CH1 (GPIO 10): đèn LED
  - CH2 (GPIO 17): bơm nước
  - Hỗ trợ active-low logic (config.USB_SWITCH.INVERT_LOGIC)
  
- **neopixel_control.py** - Điều khiển 4 LED strip + 1 LED onboard
  - LED0: motor (xanh lá)
  - LED1: USB1/đèn (xanh dương)
  - LED2: USB2/bơm (tím)
  - LED3: wifi status (xanh lá = OK, đỏ = lost)
  - Onboard (GPIO 45): heartbeat khi idle
  
- **lcd_control.py** - LCD 16x2 I2C
  - Scan tự động địa chỉ I2C (config.LCD.ADDR_CAND)
  - Clear dòng trước khi ghi để tránh sót ký tự cũ

### Networking & Cloud

- **wifi_connect.py** - Kết nối WiFi + config portal
  - Load ssid/pass từ wifi_config.json
  - Vào AP mode nếu không kết nối được (ssid: ESP32_Config)
  - Có web server cấu hình wifi qua browser (http://192.168.4.1)
  
- **api_control.py** - Lấy weather từ Open-Meteo + AQI từ AQICN
  - Cache 10 phút (config.WEATHER_TTL_SEC)
  - Timeout 8s (config.HTTP_TIMEOUT_SEC)
  - Bật/tắt provider: config.OPEN_METEO_ENABLE / AQICN_ENABLE
  
- **callapi.py** - Legacy OpenWeatherMap wrapper (ít dùng)

### Hướng dẫn lấy API cho dữ liệu thời tiết/AQI

Hệ thống hỗ trợ 3 nguồn dữ liệu: Open-Meteo (thời tiết), AQICN (chất lượng không khí) và OpenWeatherMap (legacy, tùy chọn).

#### 1) Open-Meteo (không cần API key)

- Trang chính: https://open-meteo.com/
- API forecast của Open-Meteo có thể dùng trực tiếp, không yêu cầu key ở mức sử dụng cơ bản.
- Bạn chỉ cần cấu hình tọa độ và tham số request trong phần gọi API (qua api_control.py / config liên quan weather).
- Ví dụ endpoint:
  - https://api.open-meteo.com/v1/forecast?latitude=21.5942&longitude=105.8482&hourly=temperature_2m,relative_humidity_2m,precipitation,cloud_cover,wind_speed_10m

#### 2) AQICN (cần token)

- Trang lấy token: https://aqicn.org/data-platform/token/
- Đăng ký tài khoản AQICN, tạo token API, sau đó dán token vào cấu hình dự án.
- Endpoint tham khảo:
  - https://api.waqi.info/feed/geo:{lat};{lon}/?token=YOUR_TOKEN

#### 3) OpenWeatherMap (legacy, tùy chọn)

- Chỉ dùng khi bạn bật luồng legacy qua callapi.py.
- Trang đăng ký API key: https://home.openweathermap.org/users/sign_up
- Trang quản lý key: https://home.openweathermap.org/api_keys

#### 4) Cấu hình vào dự án

- Mở config.py và điền đầy đủ URL/token cho các nguồn bạn dùng.
- Bật/tắt provider bằng các cờ:
  - OPEN_METEO_ENABLE
  - AQICN_ENABLE
- Nếu không dùng provider nào, tắt cờ tương ứng để giảm request và giảm tải RAM.

#### 5) Kiểm tra nhanh sau khi cấu hình

- Khởi động thiết bị, theo dõi log của api_control.py.
- Xác nhận weather snapshot có dữ liệu (không null) và trường trạng thái fetch thành công.
- Nếu lỗi mạng/API, hệ thống vẫn chạy local control; weather sẽ fallback theo cache gần nhất.

#### 6) Lưu ý bảo mật

- Không commit API token thật vào repo public.
- Khuyến nghị tách token sang file cấu hình cục bộ (không theo dõi git) hoặc biến môi trường khi build/deploy.
- Nếu token đã lộ, thu hồi và tạo token mới ngay.

- **AI_control.py** - MQTT telemetry/RPC handler
  - Gửi sensor data mỗi 500ms (config.AI_CONTROL.CORE_TELE_INTERVAL_MS)
  - Gửi weather mỗi 15s (config.AI_CONTROL.WX_TELE_INTERVAL_MS)
  - Nhận lệnh RPC từ cloud (setMotorSpeed, setUSB1/2, setLed0, setActiveProfile, getGrowthState, chunked profile update, etc)
  - Queue publish để tránh spam broker
  
- **data_logger.py** - Ghi CSV + upload lên Google Sheets
  - Ghi samples mỗi 60s (config.DATALOGGER.SAMPLE_PERIOD_MS)
  - Upload batch mỗi 30s (config.DATALOGGER.BATCH_INTERVAL_MS)
  - File CSV structure:
    - samples_YYYYMMDD.csv: 83 cột (sensor + weather)
    - events_YYYYMMDD.csv: 9 cột (boot, cmd, error, etc)
  - Marker file (.sent_marker) tracking tiến trình upload
  - Xóa file cũ hơn 7 ngày (config.DATALOGGER.RETENTION_DAYS)

### AI Logic

- **local_AI.py** - Logic tự động điều khiển
  - Đọc plant_profiles.json để lấy ngưỡng
  - Quạt điều khiển theo nhiều yếu tố: humidity, heat index (ưu tiên hơn temp thô), VPD, trend nhiệt, hysteresis, ramping
  - 3 bậc quạt mặc định theo profile (ví dụ petunia_v1):
    - Stage 1: 26°C → 40%
    - Stage 2: 30°C → 80%
    - Stage 3: 32°C → 100%
  - Auto bơm: soil < soil_min → ON, soil > soil_max → OFF
  - Có hot-reload profile qua RPC (không cần reset ESP32)

- **plant_profiles.json** - Cấu hình ngưỡng cho từng loại cây
  - "active_profile": tên profile đang dùng
  - "profiles": dict các profile (tomato_v1, lettuce_v1, default_safe)
  - Mỗi profile có: soil_min/max, light_min_lux, hum_max, fan_stage1/2/3_temp/speed

## Cách sửa cấu hình thường gặp

### 1. Đổi GPIO pin

Sửa trong `config.py`:
```python
SENSORS = {
    "DHT_PIN": 8,   # đổi số này
    "LDR_PIN": 1,
    "SOIL_PIN": 2,
}

MOTOR = {"PIN": 18}  # quạt

USB_SWITCH = {
    "CH1_PIN": 10,  # đèn
    "CH2_PIN": 17,  # bơm
}
```

### 2. Đổi tần suất đọc sensor / upload

Sửa trong `config.py`:
```python
LOOPS = {
    "SENSOR_UPDATE_MS": 200,  # đọc sensor mỗi 200ms
}

DATALOGGER = {
    "SAMPLE_PERIOD_MS": 60000,  # ghi sample mỗi 60s
    "BATCH_INTERVAL_MS": 30000, # upload mỗi 30s
}

AI_CONTROL = {
    "CORE_TELE_INTERVAL_MS": 500,  # MQTT telemetry mỗi 500ms
}
```

### 3. Hiệu chuẩn sensor độ ẩm đất

1. Lấy cảm biến để ngoài không khí (khô hoàn toàn)
2. Chạy REPL: `from sensor_control import SensorController; s = SensorController(); print(s.read_soil_raw())`
3. Đọc giá trị raw (VD: 1200) - đây là SOIL_DRY
4. Nhúng cảm biến vào nước (ướt hoàn toàn)
5. Đọc giá trị raw (VD: 4095) - đây là SOIL_WET
6. Sửa `config.py`:
```python
SENSORS = {
    "SOIL_DRY": 1200,  # giá trị đo được khi khô (ADC thấp)
    "SOIL_WET": 4095,  # giá trị đo được khi ướt (ADC cao)
}
```

### 4. Đổi ngưỡng tự động (quạt/bơm)

Sửa `plant_profiles.json`:
```json
{
  "active_profile": "tomato_v1",
  "profiles": {
    "tomato_v1": {
      "soil_min": 35,  // bơm ON khi soil < 35%
      "soil_max": 70,  // bơm OFF khi soil > 70%
      "fan_stage1_temp": 28,  // quạt 40% khi >= 28°C
      "fan_stage2_temp": 32,  // quạt 70% khi >= 32°C
      "fan_stage3_temp": 35   // quạt 100% khi >= 35°C
    }
  }
}
```

Sau khi sửa, dùng 1 trong các cách sau để nạp profile mới:
- Đổi active profile qua RPC (hot-reload):
```json
{"method":"setActiveProfile","params":"tomato_v1"}
```
- Hoặc upload full profile qua RPC:
```json
{"method":"setProfile","params":"<entire JSON content>"}
```
- Hoặc reset ESP32 nếu bạn cập nhật file thủ công trên thiết bị.

### 5. Đổi URL Google Sheets

Sửa `config.py`:
```python
DATALOGGER = {
    "GAS_URL_ENDPOINT": "https://script.google.com/macros/s/YOUR_DEPLOYMENT_ID/exec"
}
```

### 6. Bật/tắt log debug

Sửa `config.py`:
```python
LOGGING = {
    "ENABLE_DHT_DEBUG": True,  # in log mỗi lần đọc DHT (spam)
    "ENABLE_RAM_LOG": True,    // in RAM mỗi phút
    "ENABLE_SENSOR_VERBOSE": False,  # in log khi sensor thay đổi
}
```

### 7. Cập nhật `plant_profiles.json` qua REST RPC

- Đảm bảo thiết bị đang online trên CoreIoT (trạng thái Active và vẫn dùng đúng access token).
- Chuẩn bị `plant_profiles.json` mới tại thư mục gốc dự án và xác nhận `update_config.py` có bearer token hợp lệ.
- Chạy lệnh:
  ```shell
  python update_config.py
  ```
  (các giá trị mặc định đã chứa device id, bearer token và token thiết bị; override bằng `--device-id`, `--bearer-token`, `--mqtt-token` khi cần).
- Quan sát log: script phải in đủ 3 bước `start`, `append` chunk, `commit` với thông báo THÀNH CÔNG.
- Kiểm tra lại trên dashboard (thuộc tính `profile_update_status` = `success`) hoặc gọi RPC `getActiveProfile` để xác nhận thiết bị đã nạp file mới.
- Bảo mật: bearer token là nhạy cảm, nên xoá hoặc đổi lại token sau khi hoàn tất nếu đây chỉ là credential tạm thời.

## Upload code lên ESP32

### Dùng Thonny (khuyến nghị)
1. Cài Thonny IDE
2. Tools > Options > Interpreter > MicroPython (ESP32)
3. Chọn COM port
4. File > Open... > chọn file .py
5. File > Save as... > MicroPython device > nhập tên file

### Dùng ampy (command line)
```bash
pip install adafruit-ampy
ampy --port COM3 put config.py
ampy --port COM3 put main.py
ampy --port COM3 put sensor_control.py
# ... upload tất cả file .py
ampy --port COM3 put plant_profiles.json
```

### Dùng mpremote (MicroPython official)
```bash
pip install mpremote
mpremote connect COM3 fs cp config.py :config.py
mpremote connect COM3 fs cp main.py :main.py
# ...
```

## Troubleshooting

### DHT11 lỗi timeout (Errno 116)
- Kiểm tra dây nối GPIO 8
- Đặt `config.LOGGING.ENABLE_DHT_DEBUG = True` để xem chi tiết
- DHT11 cần pullup resistor 4.7kΩ (thường có sẵn trên module)

### LCD không hiển thị
- Kiểm tra kết nối I2C (SCL=GPIO12, SDA=GPIO11)
- Chạy scan I2C:
```python
from machine import Pin, SoftI2C
i2c = SoftI2C(scl=Pin(12), sda=Pin(11))
print(i2c.scan())  # nên thấy ví dụ [33] (0x21) tại các màn khác mặc định là 27, màn của tôi có địa chỉ khác
```
- Nếu địa chỉ khác, sửa `config.LCD.I2C_ADDR`

### WiFi không kết nối
- Kiểm tra file `wifi_config.json` có đúng ssid/pass không
- Xóa file wifi_config.json để vào AP mode
- Kết nối vào AP "ESP32_Config"
- Mở browser: http://192.168.4.1
- Nhập ssid/pass mới

### Upload Google Sheets thất bại (HTTP 400)
- Kiểm tra `config.DATALOGGER.GAS_URL_ENDPOINT` đúng chưa
- Xem log trong `/logs/failed_uploads/`
- Đảm bảo Apps Script đã deploy với quyền "Anyone" (không cần đăng nhập)

### RAM hết (MemoryError)
- Bật log RAM: `config.LOGGING.ENABLE_RAM_LOG = True`
- Giảm batch size: `config.DATALOGGER.SAMPLE_MAX_BATCH_SIZE = 1`
- Giảm process lines: `config.DATALOGGER.PROCESS_LINES_PER_FILE = 2`
- Tắt weather telemetry: `config.AI_CONTROL.WX_TELE_ENABLE = False`

### Motor/USB không hoạt động
- Kiểm tra GPIO pins trong `config.py`
- Test bằng REPL:
```python
from motor_control import MotorController
m = MotorController(pin=18)
m.set_speed(50)  # 50%

from usb_switch_controller import USBSwitchController
u = USBSwitchController(ch1_pin=10, ch2_pin=17)
u.control_switch(1, 1)  # CH1 ON
```

## Files bổ sung (test/debug)

- **test_dht11.py** - Test DHT11 standalone
- **test_sender.py** - Test upload 1 dòng lên Google Sheets
- **dht_quick_test.py** - Quick DHT11 diagnostics
- **checkcauhinh.py** - In thông tin ESP32 (CPU, RAM, Flash)

## Google Apps Script

File `gas_script_final.js` cần deploy lên Google Apps Script:
1. Tạo Google Sheet mới
2. Extensions > Apps Script
3. Copy nội dung `gas_script_final.js` vào
4. Deploy > New deployment > Web app
5. Execute as: Me
6. Who has access: Anyone
7. Copy URL deployment vào `config.DATALOGGER.GAS_URL_ENDPOINT`

## Cấu trúc Google Sheet (tự động tạo)

- **Samples**: dữ liệu sensor + weather (83 cột)
- **Events**: log events (boot, cmd, error) (9 cột)
- **Error_Log**: log lỗi khi parse CSV

## Performance tips

- Giảm `CORE_TELE_INTERVAL_MS` nếu không cần realtime (500ms → 1000ms)
- Tắt weather telemetry nếu không dùng: `WX_TELE_ENABLE = False`
- Tăng `BATCH_INTERVAL_MS` nếu upload chậm (30s → 60s)
- Giảm `ADC_SAMPLES` nếu cần đọc nhanh hơn (4 → 2)

## Điều khiển qua MQTT RPC

### Sử dụng RPC Debug Terminal trên ThingsBoard

1. Vào Dashboard → Edit mode
2. Add widget → Control widgets → "RPC debug terminal"
3. Chọn device target
4. Gửi lệnh JSON format

### Danh sách RPC Methods
(Soạn trên Debug shell:{method}{"dấu cách"}{params})

#### 🧭 **RPC Help nhanh ngay trên thiết bị**

```json
{"method":"rpcHelp","params":{}}
```
Hoặc alias:

```json
{"method":"listRpc","params":{}}
```

Trả về danh mục RPC theo nhóm (status/control/profile/growth/chunked update), kèm:
- mô tả lệnh
- gợi ý params
- ví dụ payload

Mẹo: gọi `rpcHelp` trước khi thao tác để tránh nhầm method.

#### 📊 **Truy vấn trạng thái**

```json
{"method":"getStatus"}
```
Trả về: motor_speed, usb1_state, usb2_state, led_brightness

```json
{"method":"getMotorSpeed"}
```
Trả về: motor_speed (0-100)

```json
{"method":"getLedBrightness"}
```
Trả về: led_brightness (0-100)

```json
{"method":"getUsb1State"}
```
Trả về: usb1_state ("ON"/"OFF")

```json
{"method":"getUsb2State"}
```
Trả về: usb2_state ("ON"/"OFF")

```json
{"method":"getLed0"}
```
Trả về: led0 RGB {r, g, b, brightness}

```json
{"method":"getLedMode"}
```
Trả về: led_mode ("auto"/"manual")

#### 🌱 **Quản lý Plant Profile**

```json
{"method":"listProfiles"}
```
Trả về: danh sách tất cả profiles + active_profile

```json
{"method":"getActiveProfile"}
```
Trả về: tên profile đang active

```json
{"method":"setActiveProfile","params":"lettuce_v1"}
```
Đổi profile sang xà lách (hot-reload, không cần reset)

```json
{"method":"switchProfile","params":{"name":"lettuce_v1","reset_cycle":false}}
```
Đổi profile và chọn có/không reset chu kỳ trồng

**Profiles có sẵn:**
- `tomato_v1` - Cà chua (ra quả)
- `lettuce_v1` - Xà lách
- `default_safe` - Chế độ an toàn
- `dragon_fruit_v1` - Thanh long
- `bok_choy_v1` - Cải ngọt
- `cabbage_v1` - Bắp cải
- `spinach_v1` - Rau chân vịt
- `mint_v1` - Rau húng
- `kale_v1` - Cải xoăn Kale
- `update` - sau này train ra cái mới thì viết thên vào

```json
{"method":"setProfile","params":"<entire JSON content>"}
```
Upload toàn bộ file plant_profiles.json mới

#### 🌿 **Quản lý Growth State (chu kỳ phát triển)**

```json
{"method":"getGrowthState"}
```
Trả về: `plant_start_date`, `days_since_planting`, `growth_week`, `profile_name` và thêm các field dễ đọc:
- `plant_start_date_str`
- `profile_changed_date_str`
- `last_saved_str`
- `timezone`

```json
{"method":"resetPlantCycle","params":{}}
```
Reset chu kỳ trồng về đầu vụ (tuần 1)

```json
{"method":"setPlantDate","params":{"timestamp":1760000000}}
```
Đặt lại ngày bắt đầu trồng bằng Unix timestamp

```json
{"method":"setPlantDate","params":{"date_str":"10/06/2026"}}
```
Đặt ngày bắt đầu trồng theo định dạng `dd/mm/yyyy`

```json
{"method":"setPlantDate","params":{"date_str":"2026-06-10"}}
```
Đặt ngày bắt đầu trồng theo định dạng `yyyy-mm-dd`

Lưu ý parse input:
- Nếu có cả `timestamp` và `date_str`, firmware ưu tiên `timestamp`.
- Nếu thiếu cả hai, firmware trả lỗi `timestamp_or_date_str_required`.

Lưu ý: firmware hiện tại KHÔNG có `getGrowthWeek` hay `setGrowthWeek`. Dùng `getGrowthState` + `setPlantDate` để kiểm soát tuần phát triển.

**Quan trọng - Cách A (timeline hiệu dụng sẽ thay đổi):**
- Khi dùng `setPlantDate` để chỉnh tuần phát triển, hệ thống sẽ **ghi đè mốc `plant_start_date` đang dùng để tính tuổi cây**.
- Điều này giúp đổi tuần/stage ngay, nhưng cũng làm thay đổi timeline hiệu dụng từ thời điểm chỉnh trở đi.
- Log lịch sử (`samples_*.csv`, `events_*.csv`) **không bị sửa/xóa**; chỉ phần diễn giải tuổi cây trong các lần đọc sau sẽ theo mốc mới.
- Nếu cần giữ "ngày trồng thực tế ban đầu" để audit dài hạn, nên lưu thêm mốc đó ngoài hệ thống (ghi chú vận hành/dashboard note).

Ví dụ thực tế:
- Trước khi chỉnh: `plant_start_date = 01/01/2026`, hôm nay tính ra tuần 10.
- Bạn set lại để về tuần 6: hệ thống đổi `plant_start_date` gần hơn.
- Kết quả: tuần/stage giảm ngay về tuần 6, nhưng timeline hiệu dụng không còn trùng mốc trồng ban đầu.

#### 📦 **Cập nhật Profile dạng Chunked (OTA qua RPC)**

```json
{"method":"startProfileUpdate","params":{"file_size":12345,"sha256":"...64_hex..."}}
```
Khởi tạo phiên upload profile

```json
{"method":"appendProfileChunk","params":{"offset":0,"chunk":"<base64_chunk>"}}
```
Gửi từng chunk base64 theo đúng offset

```json
{"method":"commitProfileUpdate","params":{}}
```
Xác thực hash và áp dụng file mới

```json
{"method":"abortProfileUpdate","params":{}}
```
Hủy phiên upload chunked

#### 💨 **Điều khiển Quạt**

```json
{"method":"setMotorSpeed","params":0}
```
Tắt quạt

```json
{"method":"setMotorSpeed","params":50}
```
Quạt 50%

```json
{"method":"setMotorSpeed","params":100}
```
Quạt 100%

#### 💡 **Điều khiển Đèn LED**

```json
{"method":"setLedMode","params":"auto"}
```
Bật AI tự động điều khiển đèn

```json
{"method":"setLedMode","params":"manual"}
```
Tắt AI, điều khiển thủ công

```json
{"method":"setLedBrightness","params":0}
```
Tắt đèn (độ sáng 0%)

```json
{"method":"setLedBrightness","params":50}
```
Đèn 50%

```json
{"method":"setLedBrightness","params":100}
```
Đèn 100%

#### 🎨 **Màu LED RGB (NeoPixel Strip)**

```json
{"method":"setLed0","params":[255,0,0]}
```
Đỏ

```json
{"method":"setLed0","params":[0,255,0]}
```
Xanh lá

```json
{"method":"setLed0","params":[0,0,255]}
```
Xanh dương

```json
{"method":"setLed0","params":[255,255,255]}
```
Trắng

```json
{"method":"setLed0","params":[255,255,0]}
```
Vàng

```json
{"method":"setLed0","params":[255,0,255]}
```
Tím

```json
{"method":"setLed0","params":[0,255,255]}
```
Cyan

```json
{"method":"setLed0","params":[0,0,0]}
```
Tắt

**Format khác được hỗ trợ:**
```json
{"method":"setLed0","params":"#FF0000"}
```
Hex color code

```json
{"method":"setLed0","params":{"r":255,"g":0,"b":0}}
```
Object RGB

#### 💧 **Điều khiển Bơm (USB2)**

```json
{"method":"setUsb2","params":"ON"}
```
Bật bơm

```json
{"method":"setUsb2","params":"OFF"}
```
Tắt bơm

```json
{"method":"setUsb2","params":true}
```
Bật (boolean)

```json
{"method":"setUsb2","params":false}
```
Tắt (boolean)

```json
{"method":"setUsb2","params":1}
```
Bật (số)

```json
{"method":"setUsb2","params":0}
```
Tắt (số)

#### 🔌 **Điều khiển USB1 (Đèn phụ)**

```json
{"method":"setUsb1","params":"ON"}
```
Bật USB1

```json
{"method":"setUsb1","params":"OFF"}
```
Tắt USB1

### Lưu ý quan trọng

1. **Method names không phân biệt hoa/thường**
   - `getStatus`, `getstatus`, `GETSTATUS` đều OK

2. **Manual Override**
   - Các lệnh `setMotorSpeed`, `setUsb1`, `setUsb2` sẽ tự động kích hoạt **manual override** trong **5 phút**
   - LED có riêng mode `auto`/`manual` thay vì override timeout

3. **LED Mode**
   - `led_mode="auto"` → AI điều khiển theo profile (photoperiod, DLI, circadian)
   - `led_mode="manual"` → Điều khiển thủ công qua RPC

4. **Profile Hot-Reload**
   - `setActiveProfile` sẽ reload ngay lập tức, **không cần reset ESP32**
   - Attributes trên cloud sẽ cập nhật `active_profile` sau ~5 giây

5. **Growth timeline (Cách A)**
  - Hiện tại không có `getGrowthWeek` / `setGrowthWeek`.
  - Dùng `getGrowthState` để đọc tuần hiện tại.
  - Dùng `setPlantDate` để chỉnh tuần (tuần suy ra từ `plant_start_date`).
  - Khi chỉnh bằng `setPlantDate`, timeline hiệu dụng sẽ đổi theo mốc mới (log cũ giữ nguyên, nhưng tuổi cây tính từ đây sẽ theo mốc đã chỉnh).
  - `setPlantDate` hỗ trợ cả `timestamp` và `date_str` (`dd/mm/yyyy`, `yyyy-mm-dd`).

6. **Kiểm tra kết quả**
   - Sau khi gửi RPC, check attributes của device:
     - `active_profile` - Profile đang active
     - `led_mode` - Chế độ LED (auto/manual)
     - `motor_speed` - Tốc độ quạt hiện tại
     - `usb1_state`, `usb2_state` - Trạng thái USB
     - `dli_today` - DLI tích lũy hôm nay
     - `stress_index` - Chỉ số stress tích lũy
     - `energy_today_wh` - Năng lượng tiêu thụ hôm nay
     - `growth_week` - Tuần phát triển hiện tại

### Ví dụ sử dụng thực tế

**Scenario 1: Chuyển từ cà chua sang xà lách**
```json
{"method":"listProfiles"}
```
→ Xem danh sách profiles

```json
{"method":"setActiveProfile","params":"lettuce_v1"}
```
→ Đổi sang profile xà lách

```json
{"method":"getActiveProfile"}
```
→ Confirm profile đã đổi

**Scenario 2: Điều khiển đèn thủ công**
```json
{"method":"setLedMode","params":"manual"}
```
→ Tắt AI

```json
{"method":"setLed0","params":[100,150,255]}
```
→ Đặt màu xanh lam nhạt

```json
{"method":"setLedBrightness","params":80}
```
→ Giảm sáng xuống 80%

**Scenario 3: Tưới khẩn cấp**
```json
{"method":"setUsb2","params":"ON"}
```
→ Bật bơm ngay lập tức (override AI trong 5 phút)

## Attributes được publish

Device sẽ publish các attributes sau lên cloud (mỗi ~5s):

- `motor_speed` - Tốc độ quạt (0-100)
- `usb1_state`, `usb2_state` - Trạng thái USB ("ON"/"OFF")
- `led_brightness` - Độ sáng đèn (0-100)
- `led0` - Màu LED RGB [r, g, b]
- `led0_state` - Trạng thái LED ("ON"/"OFF")
- `led_mode` - Chế độ LED ("auto"/"manual")
- `active_profile` - Profile đang active (vd: "tomato_v1")
- `dli_today` - DLI tích lũy hôm nay (mol/m²/day)
- `stress_index` - Chỉ số stress tích lũy (0-100+)
- `energy_today_wh` - Năng lượng tiêu thụ hôm nay (Wh)
- `transpiration_rate_mlph` - Tốc độ thoát hơi nước (ml/h)
- `soil_predicted_2h` - Dự đoán độ ẩm đất sau 2h (%)
- `growth_week` - Tuần phát triển (1-12+)
- `emergency_mode` - Chế độ khẩn cấp (true/false)
- `rain_sim_active` - Đang mô phỏng mưa (true/false)

## Tài liệu tham khảo

- MicroPython docs: https://docs.micropython.org/
- ESP32-S3 pinout: https://docs.espressif.com/projects/esp-idf/en/latest/esp32s3/
- DHT11 datasheet: https://www.mouser.com/datasheet/2/758/DHT11-Technical-Data-Sheet-Translated-Version-1143054.pdf
- Google Apps Script limits: https://developers.google.com/apps-script/guides/services/quotas
- ThingsBoard RPC docs: https://thingsboard.io/docs/user-guide/rpc/

## License

Dự án cá nhân, sử dụng tự do.
Made by MEEEEEEEEEE! : Yolo
DM Me

F :https://www.facebook.com/chu.hieu.64477/?locale=vi_VN
Github:  (clone, cant show my real one): https://github.com/Tiu-de
