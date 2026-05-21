import network
import ujson
import time
import socket
import machine

class WiFiController:
    def __init__(self, config_file="wifi_config.json", ap_ssid="ESP32_Config"):
        self.config_file = config_file
        self.ap_ssid = ap_ssid
        self.ap = network.WLAN(network.AP_IF)
        self.sta = network.WLAN(network.STA_IF)
        self.last_status = {'connected': False, 'ip': None, 'ssid': None}

    def load_config(self):
        try:
            with open(self.config_file, "r") as f:
                return ujson.load(f)
        except Exception as e:
            print(f"Lỗi khi đọc file cấu hình: {e}")
            return None

    def save_config(self, ssid, password):
        try:
            with open(self.config_file, "w") as f:
                ujson.dump({"ssid": ssid, "password": password}, f)
            return True
        except Exception as e:
            print(f"Lỗi khi lưu file cấu hình: {e}")
            return False

    def connect_to_wifi(self, timeout=15):
        try:
            config = self.load_config()
            if not config:
                self.last_status = {'connected': False, 'ip': None, 'ssid': None}
                return self.last_status
            self.sta.active(True)
            self.sta.connect(config["ssid"], config["password"])
            for _ in range(timeout):
                if self.sta.isconnected():
                    ip = self.sta.ifconfig()[0]
                    self.last_status = {'connected': True, 'ip': ip, 'ssid': config["ssid"]}
                    return self.last_status
                time.sleep(1)
            self.sta.active(False)
            self.last_status = {'connected': False, 'ip': None, 'ssid': config["ssid"]}
            return self.last_status
        except Exception as e:
            print(f"Lỗi khi kết nối Wi-Fi: {e}")
            self.last_status = {'connected': False, 'ip': None, 'ssid': None}
            return self.last_status

    def start_config_portal(self, timeout=60):
        try:
            self.ap.active(True)
            self.ap.config(essid=self.ap_ssid, authmode=network.AUTH_OPEN)
            self.sta.active(True)
            networks = self.sta.scan()
            ssid_options = "".join([f"<option value='{s.decode()}'>{s.decode()}</option>" for s, *_ in networks])
            html = f"""<!DOCTYPE html><html><head><meta charset='utf-8'><title>Cấu hình Wi-Fi</title></head>
<body><h2>Chọn mạng Wi-Fi</h2><form method='POST'>
<label>SSID: <select name='ssid'>{ssid_options}</select></label><br><br>
<label>Mật khẩu: <input name='password' type='password'/></label><br><br>
<input type='submit' value='Kết nối'/></form></body></html>"""
            addr = socket.getaddrinfo("0.0.0.0", 80)[0][-1]
            s = socket.socket()
            s.bind(addr)
            s.listen(1)
            start_time = time.time()
            while time.time() - start_time < timeout:
                cl, addr = s.accept()
                request = cl.recv(1024).decode()
                if "POST" in request:
                    try:
                        form_data = request.split('\r\n')[-1]
                        params = dict(x.split('=') for x in form_data.split('&'))
                        ssid = params.get("ssid", "").replace("+", " ")
                        password = params.get("password", "")
                        self.save_config(ssid, password)
                        cl.send("HTTP/1.0 200 OK\r\nContent-type: text/html\r\n\r\n")
                        cl.send("<h3>Đang lưu và kết nối lại...</h3>")
                        cl.close(); s.close(); self.ap.active(False)
                        time.sleep(2); machine.reset()
                        return True
                    except Exception as e:
                        print(f"Lỗi xử lý form: {e}"); cl.close()
                else:
                    cl.send("HTTP/1.0 200 OK\r\nContent-type: text/html\r\n\r\n")
                    cl.send(html); cl.close()
                time.sleep(0.1)
            s.close(); self.ap.active(False); self.sta.active(False)
            return False
        except Exception as e:
            print(f"Lỗi config portal: {e}")
            return False

    def get_wifi_status(self):
        try:
            return {
                'connected': self.sta.isconnected(),
                'ip': self.sta.ifconfig()[0] if self.sta.isconnected() else None,
                'ssid': self.sta.config('essid') if self.sta.isconnected() else None,
                'rssi': self.sta.status('rssi') if self.sta.isconnected() else None
            }
        except Exception:
            return {'connected': False, 'ip': None, 'ssid': None, 'rssi': None}
