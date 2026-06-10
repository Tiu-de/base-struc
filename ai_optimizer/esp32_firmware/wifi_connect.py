import network
import ujson
import time
import socket

class WiFiController:
    def __init__(self, config_file="wifi_config.json", ap_ssid="ESP32_Config"):
        """Initialize the Wi-Fi controller.
        
        Args:
            config_file (str): Path to Wi-Fi configuration file (default: wifi_config.json).
            ap_ssid (str): SSID for Access Point mode (default: ESP32_Config).
        """
        self.config_file = config_file
        self.ap_ssid = ap_ssid
        self.ap = network.WLAN(network.AP_IF)
        self.sta = network.WLAN(network.STA_IF)
        self.last_status = {
            'connected': False,
            'ip': None,
            'ssid': None
        }
        self.last_scan_results = []
        self.last_scan_ts = 0

    def _scan_networks(self):
        """Scan nearby Wi-Fi APs and return unique SSIDs sorted by signal strength."""
        results = {}
        saw_error = None

        for _ in range(3):
            try:
                self.sta.active(True)
                try:
                    self.sta.disconnect()
                except Exception:
                    pass

                time.sleep(0.2)
                for item in self.sta.scan():
                    try:
                        ssid_raw = item[0]
                        rssi = item[3]
                        ssid = ssid_raw.decode("utf-8", "ignore").strip()
                        if not ssid:
                            continue
                        if ssid not in results or rssi > results[ssid]["rssi"]:
                            results[ssid] = {"ssid": ssid, "rssi": rssi}
                    except Exception:
                        continue

                if results:
                    ordered = sorted(results.values(), key=lambda x: x["rssi"], reverse=True)
                    self.last_scan_results = ordered
                    self.last_scan_ts = time.time()
                    return ordered
            except Exception as e:
                saw_error = e
                time.sleep(0.2)

        if saw_error is not None:
            print("Wi-Fi scan warn:", saw_error)

        # fallback: giữ danh sách quét thành công gần nhất để UI không luôn rỗng
        return self.last_scan_results

    def _html_escape(self, text):
        s = "" if text is None else str(text)
        s = s.replace("&", "&amp;")
        s = s.replace("<", "&lt;")
        s = s.replace(">", "&gt;")
        s = s.replace('"', "&quot;")
        s = s.replace("'", "&#39;")
        return s

    def _percent_decode(self, value, plus_as_space=False):
        """Decode percent-encoded text as UTF-8.

        plus_as_space=True is used for application/x-www-form-urlencoded payloads.
        """
        if value is None:
            return ""

        s = str(value)
        out = bytearray()
        i = 0
        n = len(s)
        while i < n:
            ch = s[i]
            if plus_as_space and ch == '+':
                out.append(32)
                i += 1
                continue
            if ch == '%' and i + 2 < n:
                hex_text = s[i + 1:i + 3]
                try:
                    out.append(int(hex_text, 16))
                    i += 3
                    continue
                except Exception:
                    pass
            try:
                out.extend(ch.encode("utf-8"))
            except Exception:
                pass
            i += 1

        try:
            return bytes(out).decode("utf-8", "ignore")
        except Exception:
            return str(value)

    def _url_decode(self, value):
        """Decode x-www-form-urlencoded text."""
        return self._percent_decode(value, plus_as_space=True)

    def _parse_form(self, body):
        data = {}
        if not body:
            return data
        for pair in body.split('&'):
            if '=' in pair:
                k, v = pair.split('=', 1)
                data[self._url_decode(k)] = self._url_decode(v)
        return data

    def _build_html(self, scan_items, message="", selected_ssid=""):
        options = []
        selected_safe = self._html_escape(selected_ssid)
        for item in scan_items:
            ssid = item.get("ssid", "")
            rssi = item.get("rssi", -100)
            ssid_safe = self._html_escape(ssid)
            sel = " selected" if ssid == selected_ssid else ""
            label = "{} ({} dBm)".format(ssid_safe, rssi)
            options.append("<option value='{}'{}>{}</option>".format(ssid_safe, sel, label))
        if not options:
            options = ["<option value=''>Khong tim thay Wi-Fi</option>"]

        message_html = ""
        if message:
            message_html = "<p><b>{}</b></p>".format(self._html_escape(message))

        return """<!DOCTYPE html>
<html>
    <head>
        <meta charset=\"utf-8\">
        <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">
        <title>Cau hinh Wi-Fi</title>
        <style>
            body {{ font-family: sans-serif; margin: 16px; max-width: 520px; }}
            select, input {{ width: 100%; padding: 8px; margin-top: 6px; margin-bottom: 10px; }}
            button {{ padding: 10px 14px; }}
            .hint {{ color: #444; font-size: 14px; }}
        </style>
    </head>
    <body>
        <h2>Cau hinh Wi-Fi</h2>
        <p class=\"hint\">Danh sach Wi-Fi/RSSI duoc cap nhat tu dong moi 8 giay.</p>
        {message_html}
        <form method=\"POST\">
            <label for=\"ssid\">SSID:</label>
            <select id=\"ssid\" name=\"ssid\" data-selected=\"{selected_ssid}\">{ssid_options}</select>
            <label for=\"password\">Mat khau:</label>
            <input name=\"password\" type=\"password\" />
            <button type=\"submit\">Ket noi</button>
        </form>
        <script>
            async function refreshNetworks() {{
                try {{
                    const select = document.getElementById('ssid');
                    const before = select.value || select.getAttribute('data-selected') || '';
                    const res = await fetch('/scan', {{ cache: 'no-store' }});
                    const data = await res.json();
                    if (!data || !data.networks) return;

                    select.innerHTML = '';
                    if (data.networks.length === 0) {{
                        const opt = document.createElement('option');
                        opt.value = '';
                        opt.textContent = 'Khong tim thay Wi-Fi';
                        select.appendChild(opt);
                    }} else {{
                        data.networks.forEach(n => {{
                            const opt = document.createElement('option');
                            opt.value = n.ssid;
                            opt.textContent = `${{n.ssid}} (${{n.rssi}} dBm)`;
                            if (n.ssid === before) opt.selected = true;
                            select.appendChild(opt);
                        }});
                        if (!select.value && select.options.length > 0) {{
                            select.options[0].selected = true;
                        }}
                    }}
                }} catch (e) {{
                    // Keep existing list if scan endpoint is temporarily unavailable.
                }}
            }}

            setInterval(refreshNetworks, 8000);
        </script>
    </body>
</html>""".format(
            message_html=message_html,
            ssid_options=''.join(options),
            selected_ssid=selected_safe
        )

    def _connect_with_credentials(self, ssid, password, timeout=15):
        """Try STA connection with provided credentials, no config save."""
        try:
            self.sta.active(True)
            try:
                if self.sta.isconnected():
                    self.sta.disconnect()
            except Exception:
                pass

            self.sta.connect(ssid, password)
            for _ in range(timeout):
                if self.sta.isconnected():
                    ip = self.sta.ifconfig()[0]
                    self.last_status = {'connected': True, 'ip': ip, 'ssid': ssid}
                    return True, ip
                time.sleep(1)
        except Exception as e:
            print("Kiem tra ket noi warn:", e)

        self.last_status = {'connected': False, 'ip': None, 'ssid': ssid}
        return False, None

    def load_config(self):
        """Load Wi-Fi configuration from file.
        
        Returns:
            dict: {'ssid': str, 'password': str} or None if file not found or error.
        """
        try:
            with open(self.config_file, "r") as f:
                return ujson.load(f)
        except Exception as e:
            print(f"Lỗi khi đọc file cấu hình: {e}")
            return None

    def save_config(self, ssid, password):
        """Save Wi-Fi configuration to file.
        
        Args:
            ssid (str): Wi-Fi SSID.
            password (str): Wi-Fi password.
        
        Returns:
            bool: True if successful, False if error.
        """
        try:
            with open(self.config_file, "w") as f:
                ujson.dump({"ssid": ssid, "password": password}, f)
            print(f"Lưu cấu hình Wi-Fi: {ssid}")
            return True
        except Exception as e:
            print(f"Lỗi khi lưu file cấu hình: {e}")
            return False

    def connect_to_wifi(self, timeout=15):
        """Attempt to connect to Wi-Fi using saved credentials.
        
        Args:
            timeout (int): Maximum time to wait for connection (seconds, default: 15).
        
        Returns:
            dict: {'status': bool, 'ip': str or None, 'ssid': str or None}
        """
        try:
            config = self.load_config()
            if not config:
                print("Không tìm thấy cấu hình Wi-Fi")
                self.last_status = {'connected': False, 'ip': None, 'ssid': None}
                return self.last_status

            ssid = config.get("ssid", "")
            password = config.get("password", "")

            # Backward compatibility: auto-decode legacy percent-encoded values.
            if isinstance(ssid, str) and ('%' in ssid):
                ssid_dec = self._percent_decode(ssid, plus_as_space=False)
                if ssid_dec:
                    ssid = ssid_dec
            if isinstance(password, str) and ('%' in password):
                pass_dec = self._percent_decode(password, plus_as_space=False)
                if pass_dec or password == "%":
                    password = pass_dec

            print(f"🔌 Đang thử kết nối Wi-Fi: {ssid}")
            self.sta.active(True)
            self.sta.connect(ssid, password)

            for _ in range(timeout):
                if self.sta.isconnected():
                    ip = self.sta.ifconfig()[0]
                    self.last_status = {
                        'connected': True,
                        'ip': ip,
                        'ssid': ssid
                    }
                    print(f"✅ Kết nối thành công! IP: {ip}")
                    # Heal old encoded config on successful connection.
                    try:
                        if ssid != config.get("ssid") or password != config.get("password"):
                            self.save_config(ssid, password)
                    except Exception:
                        pass
                    return self.last_status
                time.sleep(1)

            print("❌ Không kết nối được")
            self.sta.active(False)
            self.last_status = {'connected': False, 'ip': None, 'ssid': ssid}
            return self.last_status
        except Exception as e:
            print(f"Lỗi khi kết nối Wi-Fi: {e}")
            self.last_status = {'connected': False, 'ip': None, 'ssid': None}
            return self.last_status

    def start_config_portal(self, timeout=60):
        """Start Access Point and web server for Wi-Fi configuration.
        
        Args:
            timeout (int): Maximum time to run the portal (seconds, default: 60).
        
        Returns:
            bool: True if connected successfully, False if timeout or error.
        """
        try:
            print("Bat AP mode de cau hinh Wi-Fi...")
            self.ap.active(True)
            self.ap.config(essid=self.ap_ssid, authmode=network.AUTH_OPEN)

            self.sta.active(True)

            addr = socket.getaddrinfo("0.0.0.0", 80)[0][-1]
            s = socket.socket()
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.bind(addr)
            s.listen(1)
            s.settimeout(1)
            print("Mo trinh duyet va truy cap: http://192.168.4.1")

            start_time = time.time()
            next_scan_at = 0
            cached_ssids = []
            last_message = ""
            last_selected = ""

            while time.time() - start_time < timeout:
                now = time.time()
                if now >= next_scan_at:
                    cached_ssids = self._scan_networks()
                    next_scan_at = now + 8

                try:
                    cl, _ = s.accept()
                except OSError:
                    continue

                try:
                    request = cl.recv(2048).decode()
                except Exception:
                    cl.close()
                    continue

                req_line = ""
                try:
                    req_line = request.split("\r\n", 1)[0]
                except Exception:
                    req_line = ""

                parts = req_line.split(" ")
                method = parts[0] if len(parts) > 0 else ""
                path = parts[1] if len(parts) > 1 else "/"

                if method == "GET" and path.startswith("/scan"):
                    try:
                        cached_ssids = self._scan_networks()
                        next_scan_at = time.time() + 8
                        payload = ujson.dumps({"networks": cached_ssids})
                        cl.send("HTTP/1.0 200 OK\r\nContent-type: application/json; charset=utf-8\r\nCache-Control: no-store\r\n\r\n")
                        cl.send(payload)
                    except Exception as e:
                        print("Loi scan endpoint:", e)
                        cl.send("HTTP/1.0 500 Internal Server Error\r\nContent-type: text/plain; charset=utf-8\r\n\r\nscan error")
                    cl.close()
                    continue

                if method == "POST":
                    try:
                        body = ""
                        if "\r\n\r\n" in request:
                            body = request.split("\r\n\r\n", 1)[1]

                        params = self._parse_form(body)
                        ssid = params.get("ssid", "").strip()
                        password = params.get("password", "")
                        last_selected = ssid

                        if not ssid:
                            last_message = "Vui long chon SSID hop le."
                            response = self._build_html(cached_ssids, message=last_message, selected_ssid=last_selected)
                        else:
                            print("Thu ket noi Wi-Fi:", ssid)
                            ok, ip = self._connect_with_credentials(ssid, password, timeout=15)
                            if ok:
                                self.save_config(ssid, password)
                                last_message = "Ket noi thanh cong. AP mode da tat."
                                response = "<h3>Ket noi thanh cong!</h3><p>SSID: {}</p><p>IP: {}</p><p>Ban co the dong trang nay.</p>".format(ssid, ip)
                                try:
                                    # Send success page first; some clients close immediately after submit.
                                    cl.send("HTTP/1.0 200 OK\r\nContent-type: text/html; charset=utf-8\r\n\r\n")
                                    cl.send(response)
                                except Exception as send_err:
                                    print("Portal success response warn:", send_err)
                                try:
                                    cl.close()
                                except Exception:
                                    pass
                                try:
                                    s.close()
                                except Exception:
                                    pass
                                self.ap.active(False)
                                print("Wi-Fi OK, da thoat AP mode")
                                return True

                            last_message = "Ket noi that bai. Kiem tra mat khau roi thu lai."
                            response = self._build_html(cached_ssids, message=last_message, selected_ssid=last_selected)

                        cl.send("HTTP/1.0 200 OK\r\nContent-type: text/html; charset=utf-8\r\n\r\n")
                        cl.send(response)
                        cl.close()
                    except Exception as e:
                        print("Loi xu ly form:", e)
                        cl.close()
                else:
                    response = self._build_html(cached_ssids, message=last_message, selected_ssid=last_selected)
                    cl.send("HTTP/1.0 200 OK\r\nContent-type: text/html; charset=utf-8\r\n\r\n")
                    cl.send(response)
                    cl.close()

            print("Het thoi gian cho AP config")
            s.close()
            self.ap.active(False)
            if not self.sta.isconnected():
                self.sta.active(False)
            return False
        except Exception as e:
            print("Loi khi chay cong cau hinh:", e)
            return False

    def get_wifi_status(self):
        """Get the current Wi-Fi connection status.
        
        Returns:
            dict: {'status': bool, 'connected': bool, 'ip': str or None, 'ssid': str or None}
        """
        try:
            status = {
                'connected': self.sta.isconnected(),
                'ip': self.sta.ifconfig()[0] if self.sta.isconnected() else None,
                'ssid': self.sta.config('essid') if self.sta.isconnected() else None,
                'rssi': self.sta.status('rssi') if self.sta.isconnected() else None
            }
            return status
        except Exception as e:
            return {'connected': False, 'ip': None, 'ssid': None, 'rssi': None}
