# base-struc

Hệ thống nông nghiệp thông minh dựa trên **ESP32-S3** với điều khiển AI cục bộ.

## Cấu trúc dự án

```
ai_optimizer/esp32_firmware/   ← Firmware MicroPython chạy trên ESP32-S3
ai_mother/                     ← Server Python: phân tích dữ liệu & tối ưu profile (private)
generate_*.py                  ← Script vẽ biểu đồ phân tích
plant_profiles.json            ← Cấu hình môi trường theo loại cây
schedule.json                  ← Lịch điều khiển
```

## Phần cứng
- ESP32-S3 YoloUno
- DHT11 (nhiệt độ/độ ẩm)
- Cảm biến độ ẩm đất điện dung
- LDR (ánh sáng)
- Quạt 5V PWM, Bơm nước mini, Đèn LED grow
- LCD I2C 16x2
- NeoPixel WS2812

## Tính năng
- Điều khiển tự động (AI cục bộ + MQTT/CoreIoT)
- Ghi log CSV + đồng bộ Google Sheets
- Plant profile: VPD, DLI, Stress Index
- Hoạt động offline khi mất mạng
