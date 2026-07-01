# callapi.py
# Nhẹ RAM + ổn định kết nối cho MicroPython (ESP32/ESP32-S3)
# Giữ nguyên API: get_weather(timeout=10) -> dict hoặc None
import gc
try:
    import usocket as socket
except ImportError:
    import socket
try:
    import ussl as ssl
except ImportError:
    import ssl # type: ignore
try:
    import ujson as json
except ImportError:
    import json
# ================== CẤU HÌNH ==================
API_KEY = "<REDACTED_OPENWEATHER_API_KEY>"
CITY = "Thai Nguyen,vn"
UNITS = "metric"
LANG = "en" # hoặc "vi"
HOST = "api.openweathermap.org"
PORT = 443
# Giới hạn tối đa body đọc về (bytes) để tránh OOM
MAX_BODY_BYTES = 10 * 1024
# Số lần retry khi DNS/kết nối lỗi ngắn hạn
RETRY = 2
# =============================================
def url_encode(s):
    return s.replace(" ", "%20").replace(",", "%2C")
def _build_path():
    # Sử dụng link cố định như yêu cầu
    return "/data/2.5/weather?q=Thai%20Nguyen,VN&appid=<REDACTED_OPENWEATHER_API_KEY>&units=metric&lang=en"
# --------- Tiện ích đọc HTTP nhẹ RAM ----------
def _readline(sock):
    # Đọc tới \n (bao gồm \r\n) – tránh cấp phát lớn
    line = b""
    while True:
        ch = sock.read(1)
        if not ch:
            break
        line += ch
        if ch == b"\n":
            break
    return line
def _read_exact(sock, n):
    # Đọc đúng n bytes
    buf = bytearray()
    mv = memoryview(buf)
    read_total = 0
    while read_total < n:
        chunk = sock.read(n - read_total)
        if not chunk:
            break
        # Mở rộng bytearray mà không tạo bản sao lớn
        buf.extend(chunk)
        read_total += len(chunk)
    return bytes(buf)
def _dechunk(sock, max_bytes):
    # Giải mã Transfer-Encoding: chunked
    out = bytearray()
    while True:
        # Dòng kích thước chunk (hex)
        line = _readline(sock)
        if not line:
            break
        # bỏ \r\n
        size_str = line.strip().split(b";", 1)[0]
        try:
            size = int(size_str, 16)
        except Exception:
            size = 0
        if size <= 0:
            # đọc CRLF sau chunk cuối
            _ = _readline(sock)
            break
        part = _read_exact(sock, size)
        if not part:
            break
        # chặn tràn
        if len(out) + len(part) > max_bytes:
            # bỏ phần dư còn lại để đóng kết nối gọn gàng
            _ = _read_exact(sock, size - len(part))
            return None
        out.extend(part)
        # bỏ \r\n sau mỗi chunk
        _ = _readline(sock)
    return bytes(out)
def _http_get(host, port, path, timeout=10):
    """
    Trả (status_code:int, body:bytes|None).
    Hỗ trợ Content-Length, chunked, fallback đọc tới EOF.
    """
    s = None
    ss = None
    gc.collect()
    try:
        # DNS + connect
        ai = socket.getaddrinfo(host, port)
        addr = ai[0][-1]
        s = socket.socket()
        try:
            s.settimeout(timeout)
        except Exception:
            pass
        s.connect(addr)
        # SSL với SNI (nếu khả dụng)
        if ssl and hasattr(ssl, "wrap_socket"):
            try:
                ss = ssl.wrap_socket(s, server_hostname=host)
            except TypeError:
                ss = ssl.wrap_socket(s)
        else:
            ss = s
        # Gửi request
        req = (
            "GET {} HTTP/1.1\r\n"
            "Host: {}\r\n"
            "User-Agent: MicroPython\r\n"
            "Accept: application/json\r\n"
            "Connection: close\r\n\r\n"
        ).format(path, host)
        ss.write(req)
        # --- đọc status line
        status_line = _readline(ss)
        if not status_line:
            return 0, None
        parts = status_line.split()
        try:
            status = int(parts[1])
        except Exception:
            status = 0
        # --- đọc header
        transfer_chunked = False
        content_len = None
        # Giới hạn số header line đọc để tránh vòng lặp vô hạn
        for _ in range(64):
            line = _readline(ss)
            if not line or line in (b"\r\n", b"\n"):
                break
            low = line.lower()
            if low.startswith(b"transfer-encoding:") and b"chunked" in low:
                transfer_chunked = True
            elif low.startswith(b"content-length:"):
                try:
                    content_len = int(line.split(b":", 1)[1].strip())
                except Exception:
                    content_len = None
        if status != 200:
            # Đọc bỏ phần body để đóng kết nối gọn gàng
            try:
                if transfer_chunked:
                    _ = _dechunk(ss, MAX_BODY_BYTES)
                elif content_len is not None:
                    _ = _read_exact(ss, content_len)
                else:
                    # đọc tới EOF nhưng không lưu
                    while ss.read(256):
                        pass
            except Exception:
                pass
            return status, None
        # --- đọc body
        if transfer_chunked:
            body = _dechunk(ss, MAX_BODY_BYTES)
            return status, body
        elif content_len is not None:
            if content_len > MAX_BODY_BYTES:
                # quá lớn -> bỏ
                _ = _read_exact(ss, content_len)
                return status, None
            body = _read_exact(ss, content_len)
            return status, body
        else:
            # không có độ dài -> đọc tới EOF nhưng chặn max
            out = bytearray()
            while True:
                chunk = ss.read(512)
                if not chunk:
                    break
                if len(out) + len(chunk) > MAX_BODY_BYTES:
                    return status, None
                out.extend(chunk)
            return status, bytes(out)
    except OSError as e:
        # Trả 0 để upper layer retry
        return 0, None
    except MemoryError:
        gc.collect()
        return 0, None
    finally:
        try:
            if ss and ss is not s:
                ss.close()
        except Exception:
            pass
        try:
            if s:
                s.close()
        except Exception:
            pass
        gc.collect()
# ------------------- PUBLIC API -------------------
def get_weather(timeout=10):
    """
    Lấy thời tiết từ OpenWeatherMap, trả dict:
    {
      "city","temp","feels_like","humidity","pressure",
      "wind_speed","visibility","weather_main","weather_desc","cloudiness","rain_1h"
    }
    hoặc None nếu lỗi.
    """
    gc.collect()
    path = _build_path()
    status = 0
    body = None
    # Retry khi lỗi mạng tạm thời (DNS, timeout...)
    for _ in range(RETRY):
        status, body = _http_get(HOST, PORT, path, timeout=timeout)
        if status == 200 and body:
            break
        try:
            import time
            time.sleep(0.1)
        except Exception:
            pass
    if status != 200 or not body:
        return None
    try:
        # body có thể là bytes
        if isinstance(body, bytes):
            try:
                text = body.decode()
            except Exception:
                text = str(body, "utf-8", "ignore")
        else:
            text = body
        obj = json.loads(text)
    except Exception:
        return None
    finally:
        body = None
        gc.collect()
    try:
        main = obj.get("main") or {}
        wind = obj.get("wind") or {}
        clouds = obj.get("clouds") or {}
        weather_arr = obj.get("weather") or [{}]
        w0 = weather_arr[0] if weather_arr else {}
        rain = obj.get("rain") or {}
        return {
            "city": obj.get("name"),
            "temp": main.get("temp"),
            "feels_like": main.get("feels_like"),
            "humidity": main.get("humidity"),
            "pressure": main.get("pressure"),
            "wind_speed": wind.get("speed"),
            "visibility": obj.get("visibility"),
            "weather_main": w0.get("main"),
            "weather_desc": w0.get("description"),
            "cloudiness": clouds.get("all"),
            "rain_1h": rain.get("1h", 0),
        }
    except Exception:
        return None