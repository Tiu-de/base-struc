# local_AI.py - Logic tự động điều khiển motor (quạt) và bơm dựa trên sensor + plant profile
# Đọc plant_profiles.json để lấy ngưỡng (nhiệt độ/độ ẩm đất/ánh sáng)
# Có 3 bậc quạt: stage1 (28°C/40%), stage2 (32°C/70%), stage3 (35°C/100%)
# Tự động bật/tắt bơm khi soil moisture thấp/cao hơn min/max

import time, gc
try:
    import ujson as json
except ImportError:
    import json

from growth_state_manager import GrowthStateManager

class LocalAIModel:
    def __init__(self, ai_controller, logger=None, profile_file="plant_profiles.json"):
        """Khởi tạo Local AI.
        ai_controller: tham chiếu đến AIControl để lấy snapshot + push command
        logger: DataLogger để ghi event
        profile_file: file JSON chứa plant profiles
        """
        self.ai = ai_controller
        self.log = logger
        self.profile_file = profile_file
        self.is_updating = False # <-- NEW: Update flag
        
        # Load decision log flag from config
        self._enable_decision_log = False
        try:
            import config as CFG
            if CFG and isinstance(getattr(CFG, "LOGGING", None), dict):
                self._enable_decision_log = bool(CFG.LOGGING.get("ENABLE_LOCAL_AI_DECISION_LOG", False))
        except Exception:
            pass
        
        # Trạng thái ghi nhớ (dùng để phát hiện edge on->off)
        self.pump_was_on = False
        self.motor_was_on = False
        self.last_commands = {}  # cache lệnh gần nhất để tránh spam
        
        # Track last decisions để chỉ log khi có thay đổi (tránh spam log)
        self.last_decision_pump = None  # {"action": "...", "reason": "...", "context": {}}
        self.last_decision_motor = None
        self.last_decision_light = None
        
        # Bơm thông minh - chu kỳ
        self.pump_last_on_time = 0
        self.pump_last_check_time = 0
        self.pump_total_runtime_sec = 0  # tổng thời gian bơm trong ngày
        
        # Lịch sử sensor (dự đoán xu hướng)
        self.temp_history = []  # [(timestamp_ms, temp), ...]
        self.soil_history = []
        self.max_history = 10
        
        # DLI (Daily Light Integral) tracking
        self.dli_today = 0.0  # mol/m²/day
        self.dli_last_update = 0
        self.dli_reset_hour = 0  # reset lúc 0h mỗi ngày
        
        # Stress Recovery Mode
        self.stress_detected = False
        self.stress_start_time = 0
        self.stress_recovery_count = 0  # số lần tưới nhẹ trong recovery
        
        # Energy Budget Optimizer
        self.energy_today_wh = 0.0  # Wh
        self.energy_last_update = 0
        self.energy_reset_hour = 0
        
        # Transpiration Rate Tracking
        self.transpiration_rate = 0.0  # ml/hour ước tính
        self.water_deficit = 0.0  # ml thiếu hụt tích lũy
        
        # Night Temperature Drop (DIF)
        self.temp_max_today = -100
        self.temp_min_tonight = 100
        self.dif_reset_hour = 0
        
        # Leaf Wetness Duration
        self.leaf_wet_start = 0
        self.leaf_wet_hours_today = 0.0
        
        # Rain Simulation pattern
        self.rain_sim_active = False
        self.rain_sim_step = 0  # 0-5 steps
        self.rain_sim_last_toggle = 0
        
        # Cumulative Stress Index
        self.stress_index = 0  # điểm stress tích lũy
        self.stress_index_last_update = 0
        self.emergency_mode = False
        
        # Motor Speed Ramping
        self.motor_current_speed = 0
        self.motor_target_speed = 0
        self.motor_ramp_rate = 5  # %/giây
        self.motor_last_ramp = 0
        
        # Soil Moisture Trend Prediction
        self.soil_predicted_2h = 50
        
        # Pump Priming
        self.pump_needs_priming = False
        self.pump_priming_step = 0  # 0=chưa, 1=đổ đầy, 2=đợi, 3=xong
        self.pump_priming_start = 0
        
        # Growth State Manager (persistent across reboots)
        self.growth_state = GrowthStateManager("growth_state.json")
        self.growth_week = 0  # cached value, updated in run_decision()
        
        # Phát hiện bất thường
        self.anomaly_count = 0
        self.last_anomaly_log = 0
        
        # State tracking to prevent spam logging
        self.last_led_state = None  # Track LED on/off to log only on change
        
        self.active_profile = {}  # profile đang dùng (load từ file)
        
        if not self._load_profiles():
            # Fallback nếu file lỗi
            self.active_profile = {
                "name": "Fallback An Toan", 
                "soil_min": 30, "soil_max": 60, 
                "light_min_lux": 1000, "light_color": [255,255,255],
                "hum_max": 90, "fan_stage1_temp": 35, "fan_stage1_speed": 100
            }
            self._log_decision("LOAD_PROFILE", "FAILED", {"file": self.profile_file})
        else:
            profile_name = self.active_profile.get("name", "N/A")
            print(f"[Local AI] Đã tải profile: {profile_name}")
            self._log_decision("LOAD_PROFILE", "SUCCESS", {"active": profile_name})

        # Sync trạng thái ban đầu (bơm + quạt)
        try:
            if hasattr(self.ai, "get_snapshot"):
                snap = self.ai.get_snapshot() or {}
                try:
                    usb2 = snap.get("usb2_state")
                    if usb2 == "ON" or (isinstance(usb2, (int, float)) and int(usb2) != 0):
                        self.pump_was_on = True
                    else:
                        self.pump_was_on = False
                except Exception:
                    self.pump_was_on = False
                try:
                    self.motor_was_on = bool(int(snap.get("motor_speed") or 0) > 0)
                except Exception:
                    self.motor_was_on = False
        except Exception:
            pass

    def _load_profiles(self):
        """Đọc plant_profiles.json và load profile đang active.
        Giải phóng RAM ngay sau khi parse xong (chỉ giữ 1 profile).
        Đồng bộ với GrowthStateManager.
        """
        try:
            with open(self.profile_file, 'r') as f:
                data = json.load(f)
            
            active_profile_name = data.get("active_profile", "default_safe")
            all_profiles = data.get("profiles", {})
            
            if active_profile_name in all_profiles:
                self.active_profile = all_profiles[active_profile_name]
            elif "default_safe" in all_profiles:
                self.active_profile = all_profiles["default_safe"]
            else:
                return False

            # Sync với growth state manager
            profile_name = self.active_profile.get("name", active_profile_name)
            old_profile = self.growth_state.state.get("profile_name", "")
            
            # Nếu profile name thay đổi, cập nhật state (KHÔNG reset cycle)
            if old_profile != profile_name:
                self.growth_state.update_profile(profile_name, reset_cycle=False)
            
            # Apply growth stage overrides nếu có
            self._apply_growth_stage_overrides()
            
            data = None; all_profiles = None; gc.collect()
            return True
            
        except Exception as e:
            print(f"Loi doc profile '{self.profile_file}': {e}")
            data = None; all_profiles = None; gc.collect()
        return False
    
    def _apply_growth_stage_overrides(self):
        """Áp dụng thresholds theo growth_week từ growth_stages nếu có."""
        try:
            growth_stages = self.active_profile.get("growth_stages")
            if not isinstance(growth_stages, dict) or len(growth_stages) == 0:
                # Profile không có growth_stages, dùng default values
                return
            
            week = self.growth_state.get_growth_week()
            
            # Tìm stage phù hợp với tuần hiện tại
            matched_stage = None
            for stage_key, stage_config in growth_stages.items():
                if self._week_matches_stage(week, stage_key):
                    matched_stage = stage_config
                    break
            
            if matched_stage:
                stage_name = matched_stage.get("stage_name", "Unknown")
                print(f"[Local AI] Applying growth stage: Week {week} -> {stage_name}")
                
                # Override các giá trị nếu có trong stage config
                for key, value in matched_stage.items():
                    if key != "stage_name":  # Skip metadata
                        self.active_profile[key] = value
                
                self._log_decision("GROWTH_STAGE", "APPLIED", {
                    "week": week,
                    "stage": stage_name,
                    "overrides": list(matched_stage.keys())
                })
            else:
                print(f"[Local AI] No growth stage matched for week {week}")
                
        except Exception as e:
            print(f"[Local AI] Error applying growth stages: {e}")
    
    def _week_matches_stage(self, week, stage_key):
        """Kiểm tra xem tuần hiện tại có khớp với stage key không.
        
        Stage key formats:
        - "1-2": tuần 1 đến 2
        - "3-6": tuần 3 đến 6
        - "7+": tuần 7 trở lên
        - "13+": tuần 13 trở lên
        """
        try:
            if "+" in stage_key:
                # Format: "7+" -> week >= 7
                min_week = int(stage_key.replace("+", ""))
                return week >= min_week
            elif "-" in stage_key:
                # Format: "1-2" -> 1 <= week <= 2
                parts = stage_key.split("-")
                min_week = int(parts[0])
                max_week = int(parts[1])
                return min_week <= week <= max_week
            else:
                # Single week: "5"
                return week == int(stage_key)
        except Exception:
            return False
    
    def _get_current_growth_stage_name(self):
        """Lấy tên giai đoạn phát triển hiện tại."""
        try:
            growth_stages = self.active_profile.get("growth_stages")
            if not isinstance(growth_stages, dict):
                return None
            
            week = self.growth_week
            for stage_key, stage_config in growth_stages.items():
                if self._week_matches_stage(week, stage_key):
                    return stage_config.get("stage_name", stage_key)
            
            return None
        except Exception:
            return None

    def start_update(self):
        """Báo hiệu bắt đầu quá trình cập nhật profile, tạm dừng logic AI."""
        self.is_updating = True
        self._log_decision("PROFILE_UPDATE", "START", {})
        print("[Local AI] Pausing decision logic for profile update.")

    def end_update(self, success=True):
        """Báo hiệu kết thúc quá trình cập nhật, kích hoạt lại logic AI."""
        self.is_updating = False
        self._log_decision("PROFILE_UPDATE", "END", {"success": success})
        print("[Local AI] Resuming decision logic.")

    def reload_profile(self):
        """Hot-reload profile từ file (gọi qua RPC khi sửa profile không cần reset)."""
        print("[Local AI] Dang tai lai profile (Hot-Reload)...")
        if self._load_profiles():
            profile_name = self.active_profile.get("name", "N/A")
            self._log_decision("RELOAD_PROFILE", "SUCCESS", {"active": profile_name})
            return True
        else:
            self._log_decision("RELOAD_PROFILE", "FAILED", {"file": self.profile_file})
            return False

    def _log_decision(self, reason, action, context=None, decision_type="general"):
        """Log quyết định của AI (console + DataLogger event).
        CHỈ LOG KHI QUYẾT ĐỊNH THAY ĐỔI - tránh spam log mỗi 5 giây.
        
        Args:
            reason: lý do quyết định (ví dụ: "Ngoai gio photoperiod")
            action: hành động (ví dụ: "TAT_DEN_NGHI")
            context: thông tin bổ sung
            decision_type: loại quyết định ("pump", "motor", "light", "general")
        """
        # Check if this decision is different from last one
        current_decision = {"action": action, "reason": reason, "context": context}
        
        # Compare with last decision of same type
        last_decision = None
        if decision_type == "pump":
            last_decision = self.last_decision_pump
            self.last_decision_pump = current_decision
        elif decision_type == "motor":
            last_decision = self.last_decision_motor
            self.last_decision_motor = current_decision
        elif decision_type == "light":
            last_decision = self.last_decision_light
            self.last_decision_light = current_decision
        
        # Only log if decision changed OR if it's a general/important event
        should_log = False
        if decision_type == "general":
            should_log = True  # Always log general events (profile changes, errors, etc)
        elif last_decision is None:
            should_log = True  # First decision
        elif last_decision["action"] != action or last_decision["reason"] != reason:
            should_log = True  # Decision changed
        
        if not should_log:
            return  # Skip logging if decision unchanged
        
        if self._enable_decision_log:
            print(f"[Local AI] Quyết định: {reason} -> {action}")
        if self.log:
            try:
                meta = context.copy() if context else {}
                meta["reason"] = reason  # Move reason to meta for clarity
                meta.setdefault("channel", "decision")
                evt = {
                    "kind": "ai_local",
                    "src": "local_ai",
                    "act": "decision",  # Consistent action name
                    "val": action,  # The actual decision/action taken
                    "meta": meta
                }
                self.log.log_event(evt)
            except Exception as e:
                # Log minimal error to aid debugging (avoid silent failures)
                print(f"[Local AI] Warning: log_event failed - {e}")
    
    def _log_decision_rich(self, reason, action, sensor_data=None, context=None, decision_type="general"):
        """Log quyết định với FULL CONTEXT để AI mẹ phân tích.
        Ghi tất cả state: stress, VPD, transpiration, DLI, energy, modes.
        AI mẹ sẽ đọc log này để hiểu logic Local AI.
        CHỈ LOG KHI QUYẾT ĐỊNH THAY ĐỔI.
        """
        # Check if decision changed (same logic as _log_decision)
        current_decision = {"action": action, "reason": reason}
        last_decision = None
        if decision_type == "pump":
            last_decision = self.last_decision_pump
            self.last_decision_pump = current_decision  # UPDATE tracking
        elif decision_type == "motor":
            last_decision = self.last_decision_motor
            self.last_decision_motor = current_decision  # UPDATE tracking
        elif decision_type == "light":
            last_decision = self.last_decision_light
            self.last_decision_light = current_decision  # UPDATE tracking
        
        should_log = (decision_type == "general" or 
                     last_decision is None or 
                     last_decision["action"] != action or 
                     last_decision["reason"] != reason)
        
        if not should_log:
            return  # Skip if decision unchanged
        
        if self._enable_decision_log:
            print(f"[Local AI] Quyết định (Rich): {reason} -> {action}")
        if self.log:
            try:
                # Build rich context (MicroPython compatible)
                rich_ctx = {}
                
                # Merge original context first
                if context:
                    rich_ctx.update(context)
                
                # CRITICAL: Add reason to meta (consistent with _log_decision)
                rich_ctx["reason"] = reason
                
                # Add rich monitoring data
                rich_ctx.update({
                    # === STRESS & EMERGENCY ===
                    "stress_detected": self.stress_detected,
                    "stress_index": self.stress_index,
                    "emergency_mode": self.emergency_mode,
                    "stress_recovery_count": self.stress_recovery_count,
                    
                    # === ENVIRONMENTAL INTELLIGENCE ===
                    "transpiration_rate": round(self.transpiration_rate, 2),
                    "water_deficit": round(self.water_deficit, 2),
                    "soil_predicted_2h": round(self.soil_predicted_2h, 1),
                    
                    # === LIGHT & PHOTOPERIOD ===
                    "dli_today": round(self.dli_today, 2),
                    "growth_week": self.growth_week,
                    "leaf_wet_hours": round(self.leaf_wet_hours_today, 2),
                    
                    # === ENERGY ===
                    "energy_today_wh": round(self.energy_today_wh, 2),
                    
                    # === TEMPERATURE TRACKING ===
                    "temp_max_today": round(self.temp_max_today, 1) if self.temp_max_today > -100 else None,
                    "temp_min_tonight": round(self.temp_min_tonight, 1) if self.temp_min_tonight < 100 else None,
                    
                    # === MOTOR RAMPING ===
                    "motor_current_speed": self.motor_current_speed,
                    "motor_target_speed": self.motor_target_speed,
                    
                    # === SPECIAL MODES ===
                    "rain_sim_active": self.rain_sim_active,
                    "rain_sim_step": self.rain_sim_step,
                    "pump_needs_priming": self.pump_needs_priming,
                    "anomaly_count": self.anomaly_count,
                    
                    # === PROFILE INFO ===
                    "active_profile": self.active_profile.get("name", "Unknown"),
                })
                rich_ctx.setdefault("channel", "decision_rich")
                
                # Add sensor data if provided
                if sensor_data:
                    rich_ctx["sensor_temp"] = sensor_data.get("temperature")
                    rich_ctx["sensor_hum"] = sensor_data.get("hum")
                    rich_ctx["sensor_soil"] = sensor_data.get("soil")
                    rich_ctx["sensor_lux"] = sensor_data.get("lux")
                
                evt = {
                    "kind": "ai_local",
                    "src": "local_ai",
                    "act": "decision_rich",  # Consistent with _log_decision
                    "val": action,  # The actual decision/action taken
                    "meta": rich_ctx
                }
                self.log.log_event(evt)
            except Exception as e:
                print(f"[Local AI] Lỗi log_decision_rich: {e}")
                # Fallback to simple log with CORRECT format
                try:
                    meta = context.copy() if context else {}
                    meta["reason"] = reason
                    meta.setdefault("channel", "decision")
                    evt = {"kind": "ai_local", "src": "local_ai", "act": "decision", "val": action, "meta": meta}
                    self.log.log_event(evt)
                except Exception as e2:
                    print(f"[Local AI] Warning: fallback log_event also failed - {e2}")
    
    def _calc_heat_index(self, temp, hum):
        """Tính nhiệt độ cảm nhận (heat index).
        Đơn giản hóa: HI = temp + offset * (hum - 50)
        offset lấy từ profile (mặc định 0.4)
        """
        if temp is None or hum is None:
            return temp
        offset = self.active_profile.get("heat_index_offset", 0.4)
        hi = temp + offset * (hum - 50) / 100
        return hi
    
    def _calc_vpd(self, temp, hum):
        """Tính VPD (Vapor Pressure Deficit) - kPa.
        VPD thấp (< 0.4): cây không hút nước tốt
        VPD ideal (0.8-1.2): tăng trưởng tốt nhất
        VPD cao (> 1.6): cây stress, mất nước nhanh
        """
        if temp is None or hum is None:
            return None
        try:
            # SVP (Saturated Vapor Pressure) - kPa
            # Công thức Magnus-Tetens
            import math
            svp = 0.6108 * math.exp(17.27 * temp / (temp + 237.3))
            # VP (Actual Vapor Pressure)
            vp = svp * (hum / 100.0)
            # VPD
            vpd = svp - vp
            return vpd
        except Exception:
            return None
    
    def _calc_transpiration_rate(self, temp, hum, lux, vpd):
        """Ước tính tốc độ thoát hơi nước (ml/hour).
        Transpiration ∝ (VPD × Light × Temp) / Hum
        Cao khi: nóng, khô, sáng → cây mất nước nhanh
        """
        if temp is None or hum is None or lux is None:
            return 0.0
        try:
            # Base rate (giả định cây trung bình)
            # VPD cao → thoát hơi nhanh
            vpd_factor = vpd if vpd is not None else ((100 - hum) / 100.0)
            
            # Light factor (lux → PPFD xấp xỉ)
            light_factor = min(1.0, lux / 10000.0)  # normalize 0-1
            
            # Temp factor (tăng theo nhiệt độ)
            temp_factor = max(0, (temp - 15) / 25.0)  # 15°C=0, 40°C=1
            
            # Hum factor (giảm khi ẩm cao)
            hum_factor = max(0.1, (100 - hum) / 100.0)
            
            # Tổng hợp (hệ số điều chỉnh thực nghiệm)
            rate = vpd_factor * light_factor * temp_factor * hum_factor * 50  # ml/h
            return max(0, rate)
        except Exception:
            return 0.0
    
    def _calc_root_zone_temp(self, air_temp, soil_moisture):
        """Ước tính nhiệt độ vùng rễ.
        Root temp thấp hơn air temp 2-5°C (đất ẩm → mát hơn).
        """
        if air_temp is None or soil_moisture is None:
            return air_temp
        try:
            # Đất ẩm → nhiệt dung cao → mát hơn
            cooling_offset = 2 + (soil_moisture / 50.0)  # ẩm 50% → -3°C
            root_temp = air_temp - cooling_offset
            return root_temp
        except Exception:
            return air_temp
    
    def _calc_dew_point(self, temp, hum):
        """Tính điểm sương (Dew Point) - °C.
        Công thức xấp xỉ: DP ≈ T - ((100-RH)/5)
        """
        if temp is None or hum is None:
            return None
        try:
            dew_point = temp - ((100 - hum) / 5.0)
            return dew_point
        except Exception:
            return None
    
    def _update_stress_index(self, temp, soil, vpd, hum):
        """Cập nhật Cumulative Stress Index (điểm stress tích lũy).
        Mỗi giờ tính điểm stress, reset mỗi ngày.
        """
        now = time.ticks_ms()
        
        # Reset stress index lúc 0h
        try:
            current_hour = (time.localtime()[3] + 7) % 24
            if current_hour == 0 and self.stress_index > 0:
                self._log_decision("Stress Index Reset", "NEW_DAY", {"stress_yesterday": self.stress_index})
                self.stress_index = 0
        except Exception:
            pass
        
        # Tính điểm stress mỗi phút
        if self.stress_index_last_update > 0:
            elapsed_min = time.ticks_diff(now, self.stress_index_last_update) / 60000.0
            if elapsed_min >= 1.0:  # mỗi phút
                stress_points = 0
                
                if temp is not None and temp > 35:
                    stress_points += 2
                if soil is not None and soil < 25:
                    stress_points += 3
                if vpd is not None and vpd > 1.8:
                    stress_points += 2
                if hum is not None and hum > 90:
                    stress_points += 1
                
                self.stress_index += stress_points
                self.stress_index_last_update = now
                
                # Emergency mode
                if self.stress_index > 50 and not self.emergency_mode:
                    self.emergency_mode = True
                    self._log_decision("EMERGENCY MODE", "STRESS_INDEX_HIGH", {"index": self.stress_index})
                elif self.stress_index < 20 and self.emergency_mode:
                    self.emergency_mode = False
                    self._log_decision("EMERGENCY MODE OFF", "STRESS_RECOVERED", {"index": self.stress_index})
        else:
            self.stress_index_last_update = now
    
    def _predict_soil_2h(self, soil_current, transpiration_rate):
        """Dự đoán soil moisture sau 2 giờ.
        Dựa vào transpiration rate + xu hướng.
        """
        if soil_current is None:
            return 50
        try:
            # Ước tính soil giảm bao nhiêu % sau 2h
            # Giả định: 100ml/h transpiration ≈ 5% soil/2h
            soil_loss_2h = (transpiration_rate / 100.0) * 5 * 2  # %
            predicted = soil_current - soil_loss_2h
            return max(0, min(100, predicted))
        except Exception:
            return soil_current
    
    def _calc_growth_week(self):
        """Tính tuần phát triển từ plant_start_date (persistent)."""
        try:
            return self.growth_state.get_growth_week()
        except Exception:
            return 1
    
    def _update_energy_budget(self, pump_on, motor_speed, led_brightness):
        """Tracking năng lượng tiêu thụ (Wh).
        Pump: 20W, Motor: 15W, LED: 10W
        """
        now = time.ticks_ms()
        
        # Reset năng lượng lúc 0h
        try:
            current_hour = (time.localtime()[3] + 7) % 24
            if current_hour == 0 and current_hour != self.energy_reset_hour:
                self._log_decision("Energy Reset", "NEW_DAY", {"energy_yesterday_wh": self.energy_today_wh})
                self.energy_today_wh = 0.0
                self.energy_reset_hour = 0
            elif current_hour != 0:
                self.energy_reset_hour = -1
        except Exception:
            pass
        
        # Tính năng lượng tiêu thụ
        if self.energy_last_update > 0:
            elapsed_sec = time.ticks_diff(now, self.energy_last_update) / 1000.0
            if elapsed_sec > 0 and elapsed_sec < 600:
                power_w = 0.0
                if pump_on:
                    power_w += 20.0  # Pump 20W
                if motor_speed > 0:
                    power_w += 15.0 * (motor_speed / 100.0)  # Motor 0-15W
                if led_brightness > 0:
                    power_w += 10.0 * (led_brightness / 255.0)  # LED 0-10W
                
                energy_wh = power_w * (elapsed_sec / 3600.0)
                self.energy_today_wh += energy_wh
        
        self.energy_last_update = now
    
    def _update_dli(self, lux):
        """Cập nhật DLI (Daily Light Integral).
        DLI = tích phân ánh sáng trong ngày (mol/m²/day).
        Cà chua cần 20-30 mol/m²/day, xà lách 12-16.
        Chuyển đổi: 1 lux ≈ 0.0185 µmol/m²/s (xấp xỉ)
        """
        if lux is None:
            return
        
        now = time.ticks_ms()
        
        # Reset DLI lúc 0h mỗi ngày
        try:
            current_hour = (time.localtime()[3] + 7) % 24
            if current_hour == 0 and current_hour != self.dli_reset_hour:
                self.dli_today = 0.0
                self.dli_reset_hour = 0
                self._log_decision("DLI Reset", "NEW_DAY", {"dli_yesterday": self.dli_today})
            elif current_hour != 0:
                self.dli_reset_hour = -1
        except Exception:
            pass
        
        # Tính DLI tích lũy
        if self.dli_last_update > 0:
            elapsed_sec = time.ticks_diff(now, self.dli_last_update) / 1000.0
            if elapsed_sec > 0 and elapsed_sec < 600:  # ignore nếu > 10 phút (reboot?)
                # PPFD (µmol/m²/s) ≈ lux * 0.0185
                ppfd = lux * 0.0185
                # DLI tăng thêm (mol/m²)
                dli_increment = ppfd * elapsed_sec / 1_000_000.0
                self.dli_today += dli_increment
        
        self.dli_last_update = now
    
    def _get_temp_trend(self):
        """Tính xu hướng nhiệt độ (°C/phút).
        Dương = tăng, Âm = giảm.
        """
        if len(self.temp_history) < 3:
            return 0.0
        temps = [t for _, t in self.temp_history[-3:]]
        times = [ts for ts, _ in self.temp_history[-3:]]
        try:
            dt_ms = time.ticks_diff(times[-1], times[0])
            if dt_ms <= 0:
                return 0.0
            dt_min = dt_ms / 60000.0
            trend = (temps[-1] - temps[0]) / dt_min if dt_min > 0 else 0.0
            return trend
        except Exception:
            return 0.0
    
    def _detect_stress(self, temp, soil):
        """Phát hiện cây bị stress (môi trường khắc nghiệt).
        Stress nếu:
        - Nhiệt độ > 35°C kéo dài > 2h
        - Soil < 20% (cực khô)
        - VPD > 2.0 kPa (mất nước cực nhanh)
        """
        now = time.ticks_ms()
        
        # Kiểm tra điều kiện stress
        is_stress = False
        stress_reason = []
        
        if temp is not None and temp > 35:
            # Kiểm tra nóng kéo dài
            if self.stress_start_time == 0:
                self.stress_start_time = now
            elapsed_min = time.ticks_diff(now, self.stress_start_time) / 60000.0
            if elapsed_min > 120:  # > 2 giờ
                is_stress = True
                stress_reason.append(f"Heat stress {elapsed_min/60:.1f}h")
        else:
            self.stress_start_time = 0
        
        if soil is not None and soil < 20:
            is_stress = True
            stress_reason.append(f"Drought stress (soil={soil}%)")
        
        if is_stress and not self.stress_detected:
            self.stress_detected = True
            self.stress_recovery_count = 0
            self._log_decision("STRESS DETECTED", "START_RECOVERY", {"reasons": stress_reason})
        elif not is_stress and self.stress_detected:
            self._log_decision("STRESS RECOVERED", "END_RECOVERY", {"cycles": self.stress_recovery_count})
            self.stress_detected = False
        
        return self.stress_detected
    
    def _detect_anomaly(self, temp, soil):
        """Phát hiện bất thường sensor.
        Trả về True nếu có bất thường.
        """
        if len(self.temp_history) < 2:
            return False
        
        # Nhiệt độ đột ngột tăng/giảm > 5°C trong 2 phút
        if temp is not None:
            prev_temp = self.temp_history[-1][1]
            if abs(temp - prev_temp) > 5:
                return True
        
        # Soil tăng đột ngột khi không bơm (ai đó tưới thủ công?)
        if soil is not None and len(self.soil_history) >= 2:
            prev_soil = self.soil_history[-1][1]
            if not self.pump_was_on and soil > prev_soil + 15:
                return True
        
        return False

    def _push_command(self, cmd_type, cmd_value):
        """
        Push local command but avoid duplicate commands from LocalAI.
        Returns True if actually enqueued, False if deduped or failed.
        """
        try:
            # normalize value for comparison
            def _norm(v):
                if isinstance(v, (list, tuple)):
                    return tuple(int(x) if isinstance(x, (int, float)) else int(str(x)) for x in v)
                if isinstance(v, bool):
                    return int(v)
                if isinstance(v, (int, float)):
                    return int(v)
                return v
            nval = _norm(cmd_value)

            prev = self.last_commands.get(cmd_type, None)

            # If we already requested same command previously, check actual snapshot
            # If snapshot already reflects that value then skip; otherwise we should re-send.
            if prev is not None and prev == nval:
                try:
                    # get current snapshot from AIControl (thread-safe)
                    snap = self.ai.get_snapshot() if hasattr(self.ai, "get_snapshot") else {}
                    
                    if cmd_type == "led0":
                        led0_snap = snap.get("led0")
                        if isinstance(led0_snap, (list, tuple)) and len(led0_snap) >= 3:
                            cur = tuple(int(x) & 255 for x in led0_snap[:3])
                            if cur == nval:
                                return False
                        else:
                            if snap.get("led0_state") == "OFF" and nval == (0,0,0):
                                return False
                    elif cmd_type in ("usb2", "usb1"):
                        st = snap.get(f"{cmd_type}_state") or snap.get("usb2_state") or snap.get("usb1_state")
                        if isinstance(st, str):
                            st_norm = 1 if st.upper() == "ON" else 0
                        else:
                            try:
                                st_norm = int(bool(st))
                            except Exception:
                                st_norm = 1 if st else 0
                        if st_norm == int(nval):
                            return False
                    elif cmd_type == "motor_speed":
                        ms = snap.get("motor_speed")
                        try:
                            if int(ms or 0) == int(nval):
                                return False
                        except Exception:
                            pass
                    # (Code chống lặp cho các type khác...)
                except Exception:
                    pass

            cmd_dict = {"type": cmd_type, "value": cmd_value}
            if hasattr(self.ai, 'push_local_command'):
                ok = self.ai.push_local_command(cmd_dict)
                if ok:
                    # update last_commands on success
                    self.last_commands[cmd_type] = nval
                else:
                    print("[Local AI] Warning: push_local_command returned False for", cmd_dict)
                    self._log_decision("PUSH_CMD", "FAILED", {"cmd": cmd_dict})
                return ok
            else:
                print("[Local AI] Lỗi: Không tìm thấy 'ai.push_local_command'.")
                self._log_decision("PUSH_CMD", "ERROR", {"reason": "no push_local_command method"})
            return False
        except Exception as e:
            print(f"[Local AI] Lỗi _push_command: {e}")
            return False

    def run_decision_logic(self, sensors, weather):
        """
        Chạy logic cho cả tưới tiêu và chiếu sáng.
        """
        # === BƯỚC 0: KIỂM TRA TRẠNG THÁI CẬP NHẬT ===
        if self.is_updating:
            # Nếu đang trong quá trình cập nhật, bỏ qua toàn bộ logic
            return
        # === KẾT THÚC BƯỚC 0 ===
        
        # === BƯỚC 1: ĐỒNG BỘ TRẠNG THÁI (Bơm & Quạt) ===
        snap = {}
        try:
            if hasattr(self.ai, "get_snapshot"):
                snap = self.ai.get_snapshot() or {} # Lấy snapshot một lần
                
                # 1A: Đồng bộ `self.pump_was_on`
                try:
                    usb2 = snap.get("usb2_state")
                    if usb2 == "ON" or (isinstance(usb2, (int, float)) and int(usb2) != 0):
                        self.pump_was_on = True
                    else:
                        self.pump_was_on = False
                except Exception:
                    pass

                # 1B: Đồng bộ `self.motor_was_on`
                try:
                    self.motor_was_on = bool(int(snap.get("motor_speed") or 0) > 0)
                except Exception:
                    pass

                # 1C: Đồng bộ `self.last_commands` (để chống lặp)
                try:
                    led0_snap = snap.get("led0", None)
                    if isinstance(led0_snap, (list, tuple)) and len(led0_snap) >= 3:
                        self.last_commands["led0"] = tuple(int(x) & 255 for x in led0_snap[:3])
                    else:
                        if snap.get("led0_state") == "OFF":
                            self.last_commands["led0"] = (0, 0, 0)
                    
                    usb2_snap = snap.get("usb2_state")
                    if usb2_snap == "ON":
                        self.last_commands["usb2"] = 1
                    elif usb2_snap == "OFF":
                        self.last_commands["usb2"] = 0
                except Exception:
                    pass
        except Exception:
            pass
        # === KẾT THÚC BƯỚC 1 ===


        # --- 2. Logic Tưới tiêu THÔNG MINH (Chu kỳ + Recovery + Rain Sim + Transpiration + VPD + Weather) ---
        soil = sensors.get("soil")
        temp = sensors.get("temperature")
        hum = sensors.get("hum")
        lux = sensors.get("lux")
        
        # Tính VPD và Transpiration
        vpd = self._calc_vpd(temp, hum)
        transpiration_rate = self._calc_transpiration_rate(temp, hum, lux, vpd)
        self.transpiration_rate = transpiration_rate
        
        # Tính Root Zone Temperature
        root_temp = self._calc_root_zone_temp(temp, soil)
        
        # Tính Dew Point
        dew_point = self._calc_dew_point(temp, hum)
        
        # Update Cumulative Stress Index
        self._update_stress_index(temp, soil, vpd, hum)
        
        # Dự đoán Soil sau 2h
        self.soil_predicted_2h = self._predict_soil_2h(soil, transpiration_rate)
        
        # Tích lũy water deficit (ml thiếu hụt)
        now = time.ticks_ms()
        if self.pump_last_check_time > 0:
            elapsed_min = time.ticks_diff(now, self.pump_last_check_time) / 60000.0
            self.water_deficit += transpiration_rate * (elapsed_min / 60.0)
        
        # Phát hiện stress
        in_stress = self._detect_stress(temp, soil)
        
        if soil is not None and weather is not None:
            SOIL_MIN = self.active_profile.get("soil_min", 30)
            SOIL_MAX = self.active_profile.get("soil_max", 60)
            PUMP_CYCLE_SEC = self.active_profile.get("pump_cycle_sec", 30)
            PUMP_WAIT_MIN = self.active_profile.get("pump_wait_min", 5)
            PUMP_MORNING_HOUR = self.active_profile.get("pump_morning_hour", 7)
            PUMP_PREFER_NIGHT = self.active_profile.get("pump_prefer_night", True)
            
            # Lấy thông tin weather (24h, 48h, 72h)
            rain_next_24h = 0
            rain_next_48h = 0
            rain_next_72h = 0
            temp_max_24h = 0
            temp_max_48h = 0
            temp_max_72h = 0
            
            if isinstance(weather, dict):
                weather_data_inner = weather.get('data') if isinstance(weather.get('data'), dict) else weather
                if isinstance(weather_data_inner, dict):
                    rain_next_24h = weather_data_inner.get("om_sum_precip_24h_mm", 0)
                    temp_max_24h = weather_data_inner.get("om_tmax_24h_c", 0)
                    # 48h: dữ liệu thực từ API (giờ 24-47)
                    rain_next_48h = weather_data_inner.get("om_sum_precip_48h_mm", rain_next_24h * 1.5)
                    temp_max_48h = weather_data_inner.get("om_tmax_48h_c", temp_max_24h)
                    # 72h: ước tính (forecast_days=2 không đủ data)
                    rain_next_72h = rain_next_24h * 2.0
                    temp_max_72h = temp_max_24h

            will_rain = rain_next_24h > 2.0
            will_be_hot = temp_max_24h > 35
            
            # Weather-based strategy (48-72h)
            multi_day_rain = rain_next_48h > 10 and rain_next_72h > 15
            multi_day_hot = temp_max_48h > 36 and temp_max_72h > 36
            
            # Điều chỉnh soil target theo dự báo dài hạn
            soil_target_min = SOIL_MIN
            soil_target_max = SOIL_MAX
            strategy = "normal"
            
            if multi_day_rain:
                # 3 ngày liên tục mưa → giữ soil thấp, tránh úng
                soil_target_min = SOIL_MIN - 5
                soil_target_max = SOIL_MAX - 10
                strategy = "rain_forecast"
            elif multi_day_hot:
                # 3 ngày liên tục nóng → tạo dự trữ nước
                soil_target_min = SOIL_MIN + 5
                soil_target_max = SOIL_MAX - 5
                strategy = "heat_forecast"
            
            # Tính VPD để điều chỉnh tưới
            temp = sensors.get("temperature")
            hum = sensors.get("hum")
            vpd = self._calc_vpd(temp, hum)
            
            vpd_adjust = 0
            if vpd is not None:
                if vpd < 0.4:
                    # VPD thấp → cây không hút nước → giảm tưới
                    vpd_adjust = -5
                    strategy += "+low_vpd"
                elif vpd > 1.6:
                    # VPD cao → cây mất nước nhanh → tăng tưới
                    vpd_adjust = +5
                    strategy += "+high_vpd"
            
            soil_target_min = max(20, soil_target_min + vpd_adjust)
            soil_target_max = min(100, soil_target_max + vpd_adjust)
            
            # Lấy giờ hiện tại (UTC+7)
            try:
                current_hour = (time.localtime()[3] + 7) % 24
            except Exception:
                current_hour = 12
            
            is_night = current_hour >= 22 or current_hour <= 6
            is_morning = current_hour >= 6 and current_hour <= 9
            
            now = time.ticks_ms()
            pump_cycle_ms = PUMP_CYCLE_SEC * 1000
            pump_wait_ms = PUMP_WAIT_MIN * 60 * 1000
            
            # EMERGENCY MODE (ưu tiên tuyệt đối)
            if self.emergency_mode and soil < 35:
                # Stress Index quá cao → tưới ngay lập tức
                if not self.pump_was_on:
                    try:
                        self._log_decision_rich("EMERGENCY WATER", "PUMP_ON", sensors, {"index": self.stress_index, "soil": soil, "mode": "emergency"}, decision_type="pump")
                        ok = self._push_command("usb2", 1)
                        if ok:
                            self.pump_was_on = True
                            self.pump_last_on_time = now
                    except Exception:
                        pass
                else:
                    # Tưới 20s (khẩn cấp)
                    elapsed_on = time.ticks_diff(now, self.pump_last_on_time)
                    if elapsed_on > 20000:
                        try:
                            ok = self._push_command("usb2", 0)
                            if ok:
                                self.pump_was_on = False
                                self.pump_last_check_time = now
                        except Exception:
                            pass
            
            # LOGIC AN TOÀN (Ưu tiên TẮT)
            if soil > soil_target_max:
                if self.pump_was_on:
                    try:
                        self._log_decision("Dat qua am (Safety)", "TAT_PUMP_CHU_KY", {"soil": soil, "max": soil_target_max, "strategy": strategy}, decision_type="pump")
                        ok = self._push_command("usb2", 0)
                        if ok:
                            self.pump_was_on = False
                    except Exception:
                        pass
            
            # LOGIC TƯỚI DỰ PHÒNG BUỔI SÁNG (nếu dự báo nóng)
            elif will_be_hot and is_morning and soil < (soil_target_min + 10):
                # Tưới dự phòng trước khi nắng nóng
                if not self.pump_was_on:
                    elapsed_since_check = time.ticks_diff(now, self.pump_last_check_time)
                    if elapsed_since_check > pump_wait_ms:
                        try:
                            if hasattr(self.ai, "is_manual_override_active") and self.ai.is_manual_override_active(action="usb2"):
                                pass
                            else:
                                self._log_decision("Du phong sang (nong 35C)", "BAT_PUMP_DU_PHONG", {"soil": soil, "temp_max": temp_max_24h}, decision_type="pump")
                                ok = self._push_command("usb2", 1)
                                if ok:
                                    self.pump_was_on = True
                                    self.pump_last_on_time = now
                        except Exception:
                            pass
                else:
                    # Đang bơm dự phòng → check chu kỳ
                    elapsed_on = time.ticks_diff(now, self.pump_last_on_time)
                    if elapsed_on > pump_cycle_ms:
                        try:
                            self._log_decision("Ket thuc chu ky du phong", "TAT_PUMP_DU_PHONG", {"runtime_sec": PUMP_CYCLE_SEC})
                            ok = self._push_command("usb2", 0)
                            if ok:
                                self.pump_was_on = False
                                self.pump_last_check_time = now
                        except Exception:
                            pass
            
            # LOGIC STRESS RECOVERY (tưới nhẹ khi cây bị stress)
            elif in_stress and soil < (soil_target_min + 5):
                # Tưới từng đợt ngắn (5s) để cây hồi phục
                if not self.pump_was_on:
                    elapsed_since_check = time.ticks_diff(now, self.pump_last_check_time)
                    if elapsed_since_check > (pump_wait_ms * 2): # Chờ lâu hơn
                        try:
                            self._log_decision("Stress recovery cycle", "BAT_PUMP_RECOVERY", {"soil": soil, "cycle": self.stress_recovery_count + 1}, decision_type="pump")
                            ok = self._push_command("usb2", 1)
                            if ok:
                                self.pump_was_on = True
                                self.pump_last_on_time = now
                                self.stress_recovery_count += 1
                        except Exception:
                            pass
                else:
                    # Tưới 5s rồi tắt
                    elapsed_on = time.ticks_diff(now, self.pump_last_on_time)
                    if elapsed_on > 5000:  # 5s
                        try:
                            self._log_decision("Ket thuc recovery cycle", "TAT_PUMP_RECOVERY", {"cycle": self.stress_recovery_count}, decision_type="pump")
                            ok = self._push_command("usb2", 0)
                            if ok:
                                self.pump_was_on = False
                                self.pump_last_check_time = now
                        except Exception:
                            pass
            
            # LOGIC RAIN SIMULATION (soil rất khô < soil_target_min - 5, tức là dưới ngưỡng bình thường rõ rệt)
            elif soil < max(15, soil_target_min - 5) and not will_rain:
                # Rain simulation: 5s ON → 10s OFF × 3 lần
                if not self.rain_sim_active:
                    self.rain_sim_active = True
                    self.rain_sim_step = 0
                    self.rain_sim_last_toggle = now
                    self._log_decision_rich("RAIN SIMULATION START", "SPECIAL_MODE", sensors, {"soil": soil, "mode": "rain_sim", "will_rain": will_rain})
                
                if self.rain_sim_active:
                    elapsed = time.ticks_diff(now, self.rain_sim_last_toggle)
                    
                    # Pattern: ON(5s) OFF(10s) ON(5s) OFF(10s) ON(5s) OFF(10s)
                    if self.rain_sim_step % 2 == 0:  # ON phase (step 0,2,4)
                        if not self.pump_was_on:
                            try:
                                ok = self._push_command("usb2", 1)
                                if ok:
                                    self.pump_was_on = True
                                    self.rain_sim_last_toggle = now
                            except Exception:
                                pass
                        elif elapsed > 5000:  # 5s ON → next
                            try:
                                ok = self._push_command("usb2", 0)
                                if ok:
                                    self.pump_was_on = False
                                    self.rain_sim_step += 1
                                    self.rain_sim_last_toggle = now
                            except Exception:
                                pass
                    else:  # OFF phase (step 1,3,5)
                        if elapsed > 10000:  # 10s OFF → next
                            self.rain_sim_step += 1
                            self.rain_sim_last_toggle = now
                            if self.rain_sim_step >= 6:  # kết thúc 3 chu kỳ
                                self.rain_sim_active = False
                                self._log_decision("RAIN SIMULATION END", "COMPLETED", {"cycles": 3})
            
            # LOGIC TƯỚI THÔNG THƯỜNG (soil < min)
            elif soil < soil_target_min:
                if will_rain:
                    # Có mưa → không tưới
                    if self.pump_was_on:
                        try:
                            if hasattr(self.ai, "is_manual_override_active") and self.ai.is_manual_override_active(action="usb2"):
                                pass
                            else:
                                self._log_decision("Co mua (skip tuoi)", "TAT_PUMP_DU_DOAN_MUA", {"rain_mm": rain_next_24h, "strategy": strategy})
                                ok = self._push_command("usb2", 0)
                                if ok:
                                    self.pump_was_on = False
                        except Exception:
                            pass
                else:
                    # Không mưa → tưới theo chu kỳ
                    
                    # Kiểm tra Root Zone Temperature
                    root_temp_ok = True
                    if root_temp is not None:
                        if root_temp > 30:
                            # Root quá nóng → TĂNG tưới (làm mát)
                            adjusted_wait_min = max(2, PUMP_WAIT_MIN - 3)
                            strategy += "+hot_root"
                        elif root_temp < 15:
                            # Root quá lạnh → GIẢM tưới (rễ hút kém)
                            adjusted_wait_min = PUMP_WAIT_MIN + 3
                            root_temp_ok = False
                            strategy += "+cold_root"
                    
                    # Kiểm tra Dew Point (tránh tưới khi sắp có sương)
                    near_dew = False
                    if dew_point is not None and temp is not None:
                        if abs(temp - dew_point) < 2:
                            near_dew = True
                            strategy += "+near_dew"
                    
                    # Kiểm tra ưu tiên ban đêm (tiết kiệm điện)
                    can_pump = True
                    if PUMP_PREFER_NIGHT and not is_night and not is_morning:
                        # Giữa trưa → chờ đến tối (trừ khi quá khô)
                        if soil > (soil_target_min - 10):
                            can_pump = False
                    
                    # Block nếu gần dew point
                    if near_dew:
                        can_pump = False
                    
                    # Block nếu root quá lạnh
                    if not root_temp_ok:
                        can_pump = False
                    
                    # Kiểm tra Pump Priming (bơm lâu không dùng)
                    idle_hours = 0
                    if can_pump and self.pump_last_check_time > 0:
                        idle_hours = time.ticks_diff(now, self.pump_last_check_time) / 3600000.0
                        if idle_hours > 12:
                            self.pump_needs_priming = True
                    
                    if can_pump:
                        # Điều chỉnh chu kỳ theo Transpiration Rate
                        adjusted_wait_min = PUMP_WAIT_MIN
                        if self.transpiration_rate > 100:  # thoát hơi nhanh > 100ml/h
                            adjusted_wait_min = max(2, PUMP_WAIT_MIN - 2)  # rút ngắn chờ
                        elif self.transpiration_rate < 30:  # thoát hơi chậm
                            adjusted_wait_min = PUMP_WAIT_MIN + 2  # kéo dài chờ
                        
                        adjusted_wait_ms = adjusted_wait_min * 60 * 1000
                        
                        if not self.pump_was_on:
                            # Chưa bơm → kiểm tra thời gian chờ
                            elapsed_since_check = time.ticks_diff(now, self.pump_last_check_time)
                            if elapsed_since_check > adjusted_wait_ms or self.pump_last_check_time == 0:
                                try:
                                    if hasattr(self.ai, "is_manual_override_active") and self.ai.is_manual_override_active(action="usb2"):
                                        pass
                                    else:
                                        # Pump Priming nếu cần
                                        if self.pump_needs_priming and self.pump_priming_step == 0:
                                            self._log_decision("PUMP PRIMING", "START_PRIME", {"idle_hours": idle_hours})
                                            ok = self._push_command("usb2", 1)
                                            if ok:
                                                self.pump_was_on = True
                                                self.pump_priming_start = now
                                                self.pump_priming_step = 1
                                        else:
                                            # Bơm bình thường
                                            self._log_decision("Dat kho (tuoi chu ky)", "BAT_PUMP_CHU_KY", {"soil": soil, "min": soil_target_min, "wait_min": adjusted_wait_min, "transp": self.transpiration_rate, "root_temp": root_temp})
                                            ok = self._push_command("usb2", 1)
                                        if ok:
                                            self.pump_was_on = True
                                            self.pump_last_on_time = now
                                except Exception:
                                    pass
                        else:
                            # Đang bơm → check thời gian chu kỳ
                            elapsed_on = time.ticks_diff(now, self.pump_last_on_time)
                            if elapsed_on > pump_cycle_ms:
                                try:
                                    self._log_decision("Ket thuc chu ky bom", "TAT_PUMP_CHU_KY", {"runtime_sec": PUMP_CYCLE_SEC})
                                    ok = self._push_command("usb2", 0)
                                    if ok:
                                        self.pump_was_on = False
                                        self.pump_last_check_time = now
                                        self.pump_total_runtime_sec += PUMP_CYCLE_SEC
                                except Exception:
                                    pass
            
            # LOGIC TƯỚI PROACTIVE (dự đoán soil thấp sau 2h)
            elif soil is not None and self.soil_predicted_2h < soil_target_min and soil > soil_target_min:
                # Soil hiện tại OK nhưng dự đoán sẽ thấp → tưới sớm
                if not will_rain:
                    if not self.pump_was_on:
                        elapsed_since_check = time.ticks_diff(now, self.pump_last_check_time)
                        if elapsed_since_check > (pump_wait_ms / 2):  # chờ nửa thời gian
                            try:
                                self._log_decision("PROACTIVE WATER", "SOIL_PREDICT_LOW", {"current": soil, "predict_2h": self.soil_predicted_2h})
                                ok = self._push_command("usb2", 1)
                                if ok:
                                    self.pump_was_on = True
                                    self.pump_last_on_time = now
                            except Exception:
                                pass
                    else:
                        elapsed_on = time.ticks_diff(now, self.pump_last_on_time)
                        if elapsed_on > pump_cycle_ms:
                            try:
                                ok = self._push_command("usb2", 0)
                                if ok:
                                    self.pump_was_on = False
                                    self.pump_last_check_time = now
                            except Exception:
                                pass
            
            # Lưu lịch sử soil
            self.soil_history.append((now, soil))
            if len(self.soil_history) > self.max_history:
                self.soil_history.pop(0)

        # --- 3. Logic Chiếu sáng THÔNG MINH (Photoperiod + Spectrum + DLI + Circadian Rhythm) ---
        led_mode = "auto"
        try:
            led_mode = snap.get("led_mode", "auto")
        except Exception:
            pass
        
        if led_mode == "manual":
            # Manual mode → không can thiệp
            pass
        else:
            # Auto mode → AI điều khiển theo photoperiod + DLI + circadian
            lux = sensors.get("lux")
            if lux is not None:
                # Cập nhật DLI
                self._update_dli(lux)
                
                # Tính Growth Week cho Photoperiod Shift
                self.growth_week = self._calc_growth_week()
                
                LIGHT_MIN = self.active_profile.get("light_min_lux", 200)
                LIGHT_COLOR_BASE = self.active_profile.get("light_color", [255, 255, 255])
                LIGHT_OFF_THRESHOLD = LIGHT_MIN * 1.2
                
                # Photoperiod Shift Automation (theo tuần)
                PHOTOPERIOD_HOURS_BASE = self.active_profile.get("photoperiod_hours", 14)
                PHOTOPERIOD_HOURS = PHOTOPERIOD_HOURS_BASE
                photoperiod_mode = "normal"
                
                if self.growth_week <= 2:
                    PHOTOPERIOD_HOURS = 18  # mạ: 18h
                    photoperiod_mode = "seedling"
                elif self.growth_week <= 4:
                    PHOTOPERIOD_HOURS = 16  # sinh trưởng: 16h
                    photoperiod_mode = "vegetative"
                elif self.growth_week <= 8:
                    PHOTOPERIOD_HOURS = 14  # ra hoa/quả: 14h
                    photoperiod_mode = "flowering"
                else:
                    PHOTOPERIOD_HOURS = 12  # thu hoạch: 12h
                    photoperiod_mode = "harvest"
                
                # Adaptive Circadian Rhythm - điều chỉnh màu theo giờ
                try:
                    current_hour = (time.localtime()[3] + 7) % 24
                except Exception:
                    current_hour = 12
                
                # Tính màu đèn theo circadian rhythm
                LIGHT_COLOR_ON = list(LIGHT_COLOR_BASE)
                circadian_mode = "normal"
                
                # Light Spectrum Optimization (theo giai đoạn)
                spectrum_adjust = [1.0, 1.0, 1.0]  # R, G, B multiplier
                if self.growth_week <= 2:
                    # Mạ: Blue 70%, Red 30% (tăng trưởng lá)
                    spectrum_adjust = [0.3, 1.0, 1.4]
                    circadian_mode = "seedling_blue"
                elif self.growth_week <= 4:
                    # Sinh trưởng: cân bằng
                    spectrum_adjust = [1.0, 1.0, 1.0]
                elif self.growth_week <= 8:
                    # Ra hoa/quả: Red 70%, Blue 30%
                    spectrum_adjust = [1.4, 1.0, 0.6]
                    circadian_mode = "flowering_red"
                else:
                    # Thu hoạch: Red 80%
                    spectrum_adjust = [1.6, 0.9, 0.4]
                    circadian_mode = "harvest_red"
                
                # Apply spectrum
                LIGHT_COLOR_ON = [int(LIGHT_COLOR_BASE[i] * spectrum_adjust[i]) for i in range(3)]
                
                # Circadian override (trong ngày) - chỉ fine-tune thêm
                if current_hour >= 6 and current_hour < 10:
                    # Sáng sớm: tăng xanh dương thêm chút
                    LIGHT_COLOR_ON[2] = min(255, int(LIGHT_COLOR_ON[2] * 1.05))
                elif current_hour >= 14 and current_hour < 18:
                    # Chiều: tăng đỏ thêm chút
                    LIGHT_COLOR_ON[0] = min(255, int(LIGHT_COLOR_ON[0] * 1.05))
                
                # Stress mode → giảm ánh sáng 50% (giảm quang hợp)
                if in_stress:
                    LIGHT_COLOR_ON = [int(c * 0.5) for c in LIGHT_COLOR_ON]
                    circadian_mode += "+stress_dim"
                
                # Emergency mode → giảm 70%
                if self.emergency_mode:
                    LIGHT_COLOR_ON = [int(c * 0.3) for c in LIGHT_COLOR_ON]
                    circadian_mode += "+emergency"
                
                PHOTOPERIOD_HOURS_FINAL = PHOTOPERIOD_HOURS
                LIGHT_START_HOUR = self.active_profile.get("light_start_hour", 6)
                DLI_TARGET = self.active_profile.get("dli_target", 20)  # mol/m²/day
                
                # Lấy giờ hiện tại (UTC+7)
                try:
                    current_hour = (time.localtime()[3] + 7) % 24
                except Exception:
                    current_hour = 12
                
                light_end_hour = (LIGHT_START_HOUR + PHOTOPERIOD_HOURS_FINAL) % 24
                
                # Kiểm tra có trong khung giờ chiếu sáng không
                in_light_period = False
                if LIGHT_START_HOUR < light_end_hour:
                    in_light_period = (current_hour >= LIGHT_START_HOUR and current_hour < light_end_hour)
                else:
                    # Qua nửa đêm (VD: 22h-6h)
                    in_light_period = (current_hour >= LIGHT_START_HOUR or current_hour < light_end_hour)
                
                # Kiểm tra DLI đã đủ chưa
                dli_sufficient = self.dli_today >= DLI_TARGET
                
                if not in_light_period:
                    # Ngoài giờ → BẮT BUỘC TẮT (cây cần nghỉ)
                    try:
                        self._log_decision("Ngoai gio photoperiod", "TAT_DEN_NGHI", {"hour": current_hour, "dli": self.dli_today, "week": self.growth_week, "mode": photoperiod_mode}, decision_type="light")
                        self._push_command("led0", [0,0,0])
                    except Exception:
                        pass
                elif dli_sufficient:
                    # Đã đủ DLI → tắt đèn (tiết kiệm điện)
                    try:
                        self._log_decision("DLI du", "TAT_DEN_TIET_KIEM", {"dli": self.dli_today, "target": DLI_TARGET}, decision_type="light")
                        self._push_command("led0", [0,0,0])
                    except Exception:
                        pass
                else:
                    # Trong giờ + DLI chưa đủ → bật/tắt theo lux
                    if LIGHT_MIN == 0:
                        # light_min_lux=0 trong profile → LED bổ sung bị tắt hoàn toàn (cây không cần đèn nhân tạo)
                        pass
                    elif lux < LIGHT_MIN:
                        try:
                            if self.last_led_state != "ON":  # Only log on state change
                                self._log_decision("Troi toi (Circadian)", "BAT_DEN_LED0", {"lux": lux, "dli": self.dli_today, "mode": circadian_mode}, decision_type="light")
                                self.last_led_state = "ON"
                            self._push_command("led0", [int(LIGHT_COLOR_ON[0]) & 255, int(LIGHT_COLOR_ON[1]) & 255, int(LIGHT_COLOR_ON[2]) & 255])
                        except Exception:
                            pass
                    elif lux > LIGHT_OFF_THRESHOLD:
                        try:
                            if self.last_led_state != "OFF":  # Only log on state change
                                self._log_decision("Du sang tu nhien", "TAT_DEN_LED0", {"lux": lux, "dli": self.dli_today}, decision_type="light")
                                self.last_led_state = "OFF"
                            self._push_command("led0", [0,0,0])
                        except Exception:
                            pass
                        
        # --- 4. Logic Quạt THÔNG MINH (Heat Index + Xu hướng + Gió + DIF + Leaf Wetness) ---
        
        try:
            if hasattr(self.ai, "is_manual_override_active") and self.ai.is_manual_override_active(action="motor"):
                # Manual override → không can thiệp
                pass
            else:
                temp = sensors.get("temperature")
                hum = sensors.get("hum")
                
                # Night Temperature Drop (DIF) tracking
                try:
                    current_hour = (time.localtime()[3] + 7) % 24
                    is_night = current_hour >= 22 or current_hour <= 6
                    
                    if current_hour == 0 and current_hour != self.dif_reset_hour:
                        # Reset DIF lúc 0h
                        self._log_decision("DIF Reset", "NEW_DAY", {"temp_max": self.temp_max_today, "temp_min": self.temp_min_tonight})
                        self.temp_max_today = -100
                        self.temp_min_tonight = 100
                        self.dif_reset_hour = 0
                    elif current_hour != 0:
                        self.dif_reset_hour = -1
                    
                    # Track min/max
                    if temp is not None:
                        if is_night:
                            self.temp_min_tonight = min(self.temp_min_tonight, temp)
                        else:
                            self.temp_max_today = max(self.temp_max_today, temp)
                except Exception:
                    is_night = False
                
                # Leaf Wetness Duration tracking
                leaf_wet = False
                if hum is not None and hum > 90:
                    if self.leaf_wet_start == 0:
                        self.leaf_wet_start = time.ticks_ms()
                    leaf_wet = True
                else:
                    if self.leaf_wet_start > 0:
                        wet_duration_h = time.ticks_diff(time.ticks_ms(), self.leaf_wet_start) / 3600000.0
                        self.leaf_wet_hours_today += wet_duration_h
                        self.leaf_wet_start = 0
                
                # Lưu lịch sử nhiệt độ
                now = time.ticks_ms()
                if temp is not None:
                    self.temp_history.append((now, temp))
                    if len(self.temp_history) > self.max_history:
                        self.temp_history.pop(0)
                
                # Phát hiện bất thường
                if self._detect_anomaly(temp, soil):
                    self.anomaly_count += 1
                    elapsed_anomaly = time.ticks_diff(now, self.last_anomaly_log)
                    if elapsed_anomaly > 60000:  # log mỗi 1 phút
                        self._log_decision("BAT THUONG SENSOR", "CANH_BAO", {"temp": temp, "soil": soil, "count": self.anomaly_count})
                        self.last_anomaly_log = now
                
                # Tính Heat Index (nhiệt độ cảm nhận)
                heat_index = self._calc_heat_index(temp, hum)
                
                # Tính VPD (Vapor Pressure Deficit)
                vpd = self._calc_vpd(temp, hum)
                
                # Tính xu hướng nhiệt độ
                temp_trend = self._get_temp_trend()
                
                # Lấy thông tin gió từ weather
                wind_speed = 0
                if isinstance(weather, dict):
                    weather_data_inner = weather.get('data') if isinstance(weather.get('data'), dict) else weather
                    if isinstance(weather_data_inner, dict):
                        wind_speed = weather_data_inner.get("om_next_windspeed_ms", 0)
                        if wind_speed is None:
                            wind_speed = weather_data_inner.get("om_windmax_24h_ms", 0)
                
                # Lấy ngưỡng từ profile
                HUM_MAX = self.active_profile.get("hum_max", 90)
                WIND_REDUCE_THRESHOLD = self.active_profile.get("wind_speed_reduce_fan", 3.0)
                
                STAGE1_TEMP = self.active_profile.get("fan_stage1_temp")
                STAGE1_SPEED = self.active_profile.get("fan_stage1_speed")
                
                STAGE2_TEMP = self.active_profile.get("fan_stage2_temp")
                STAGE2_SPEED = self.active_profile.get("fan_stage2_speed")
                
                STAGE3_TEMP = self.active_profile.get("fan_stage3_temp")
                STAGE3_SPEED = self.active_profile.get("fan_stage3_speed")
                
                # Tính tốc độ mục tiêu
                target_speed = 0
                reason = "Moi truong OK"
                
                # Emergency mode → quạt MAX
                if self.emergency_mode:
                    target_speed = 100
                    reason = "EMERGENCY MODE (stress index high)"
                
                # Ưu tiên 0: Night Temperature Drop (DIF) control
                if is_night and temp is not None:
                    target_temp_night = self.temp_max_today - 5  # ban đêm thấp hơn 5°C
                    if self.temp_max_today > -100 and temp > target_temp_night:
                        # Quá nóng ban đêm → BẬT quạt làm mát
                        dif_deficit = temp - target_temp_night
                        dif_speed = min(100, int(dif_deficit * 20))  # 1°C → 20%
                        target_speed = max(target_speed, dif_speed)
                        reason = f"Night DIF control (target {target_temp_night:.1f}C)"
                        # Log rich context cho DIF control
                        if dif_speed > 0:
                            self._log_decision_rich("DIF_CONTROL", f"FAN_{dif_speed}%", sensors, {"target_temp_night": target_temp_night, "dif_deficit": dif_deficit, "temp_max_today": self.temp_max_today})
                
                # Ưu tiên 0b: Leaf Wetness Prevention (hum > 90% ban đêm)
                if leaf_wet and is_night:
                    # Lá ướt ban đêm → BẬT quạt nhẹ làm khô (phòng nấm)
                    target_speed = max(target_speed, 30)
                    reason = "Leaf wetness prevention (anti-fungal)"
                    self._log_decision_rich("LEAF_WETNESS", "FAN_30%", sensors, {"leaf_wet_hours_today": self.leaf_wet_hours_today, "is_night": is_night})
                
                # Ưu tiên 1: Độ ẩm cao
                if hum is not None and hum > HUM_MAX:
                    target_speed = STAGE3_SPEED or 100
                    reason = f"Do am cao ({hum}% > {HUM_MAX}%)"
                
                # Ưu tiên 2: Heat Index (dùng thay vì temp thô)
                elif heat_index is not None:
                    if STAGE3_TEMP is not None and heat_index > STAGE3_TEMP:
                        target_speed = STAGE3_SPEED or 100
                        reason = f"Heat Index cao ({heat_index:.1f}C > {STAGE3_TEMP}C)"
                    elif STAGE2_TEMP is not None and heat_index > STAGE2_TEMP:
                        target_speed = STAGE2_SPEED or 70
                        reason = f"Heat Index trung binh ({heat_index:.1f}C > {STAGE2_TEMP}C)"
                    elif STAGE1_TEMP is not None and heat_index > STAGE1_TEMP:
                        target_speed = STAGE1_SPEED or 40
                        reason = f"Heat Index nhe ({heat_index:.1f}C > {STAGE1_TEMP}C)"
                    else:
                        target_speed = 0
                        reason = "Nhiet do cam nhan OK"
                
                # Điều chỉnh theo xu hướng (nhiệt độ đang tăng nhanh)
                if temp_trend > 0.3:  # tăng > 0.3°C/phút
                    bonus_speed = min(20, int(temp_trend * 30))
                    target_speed = min(100, target_speed + bonus_speed)
                    reason += f" + Xu huong tang ({temp_trend:.2f}C/min)"
                elif temp_trend < -0.2:  # giảm nhanh
                    reduce_speed = min(15, int(abs(temp_trend) * 20))
                    target_speed = max(0, target_speed - reduce_speed)
                    reason += f" - Xu huong giam ({temp_trend:.2f}C/min)"
                
                # Điều chỉnh theo VPD
                if vpd is not None:
                    if vpd < 0.4:
                        # VPD thấp → cây không hút nước → TĂNG quạt (bay hơi)
                        vpd_bonus = min(15, int((0.4 - vpd) * 30))
                        target_speed = min(100, target_speed + vpd_bonus)
                        reason += f" + Low VPD ({vpd:.2f}kPa)"
                    elif vpd > 1.6:
                        # VPD cao → cây stress → GIẢM quạt (giữ ẩm)
                        vpd_reduce = min(20, int((vpd - 1.6) * 20))
                        target_speed = max(0, target_speed - vpd_reduce)
                        reason += f" - High VPD ({vpd:.2f}kPa)"
                
                # Giảm quạt nếu có gió tự nhiên
                if wind_speed > WIND_REDUCE_THRESHOLD:
                    reduce_wind = min(25, int((wind_speed - WIND_REDUCE_THRESHOLD) * 10))
                    target_speed = max(0, target_speed - reduce_wind)
                    reason += f" - Co gio ({wind_speed:.1f}m/s)"
                
                # Tăng quạt khi đang bơm (giúp cây hút nước + bay hơi)
                if self.pump_was_on and target_speed > 0:
                    target_speed = min(100, target_speed + 15)
                    reason += " + Dang bom (tang bay hoi)"
                
                # Fan Hysteresis (chống rung)
                # Bật ở temp cao, tắt ở temp thấp hơn 2°C
                if self.motor_was_on and target_speed == 0:
                    # Đang chạy → chỉ tắt khi temp giảm rõ rệt
                    if heat_index is not None:
                        if STAGE1_TEMP is not None and heat_index > (STAGE1_TEMP - 2):
                            # Vẫn gần ngưỡng → giữ tốc độ tối thiểu
                            target_speed = 20
                            reason = "Fan hysteresis (cooling down)"
                
                # Motor Speed Ramping (tăng/giảm từ từ)
                self.motor_target_speed = target_speed
                now_ms = time.ticks_ms()
                
                if self.motor_last_ramp > 0:
                    elapsed_sec = time.ticks_diff(now_ms, self.motor_last_ramp) / 1000.0
                    if elapsed_sec >= 1.0:  # mỗi giây
                        # Tính delta
                        delta = self.motor_target_speed - self.motor_current_speed
                        if abs(delta) > 0:
                            # Ramp 5%/giây
                            step = min(abs(delta), self.motor_ramp_rate)
                            if delta > 0:
                                self.motor_current_speed += step
                            else:
                                self.motor_current_speed -= step
                            self.motor_current_speed = max(0, min(100, int(self.motor_current_speed)))
                            self.motor_last_ramp = now_ms
                            
                            # Gửi lệnh ramped speed
                            if self._push_command("motor_speed", self.motor_current_speed):
                                self._log_decision(reason + " (ramping)", f"SET_MOTOR: {self.motor_current_speed}% (target {self.motor_target_speed}%)", {"temp": temp, "hum": hum, "HI": heat_index, "VPD": vpd, "trend": temp_trend, "wind": wind_speed}, decision_type="motor")
                                self.motor_was_on = (self.motor_current_speed > 0)
                        else:
                            # Đã đạt target
                            self.motor_last_ramp = now_ms
                else:
                    # Lần đầu
                    self.motor_current_speed = target_speed
                    self.motor_last_ramp = now_ms
                    if self._push_command("motor_speed", target_speed):
                        self._log_decision(reason, f"SET_MOTOR: {target_speed}%", {"temp": temp, "hum": hum, "HI": heat_index, "VPD": vpd, "trend": temp_trend, "wind": wind_speed}, decision_type="motor")
                        self.motor_was_on = (target_speed > 0)
        
        except Exception as e:
            print(f"Loi logic quat thong minh: {e}")
        
        # --- 5. Energy Budget Optimizer ---
        try:
            # Lấy trạng thái hiện tại
            pump_on = self.pump_was_on
            motor_speed = 0
            led_brightness = 0
            
            try:
                motor_speed = int(snap.get("motor_speed", 0))
            except Exception:
                pass
            
            try:
                led0 = snap.get("led0", [0,0,0])
                if isinstance(led0, (list, tuple)) and len(led0) >= 3:
                    led_brightness = max(led0[0], led0[1], led0[2])
            except Exception:
                pass
            
            # Update energy
            self._update_energy_budget(pump_on, motor_speed, led_brightness)
            
            # Energy budget limit (optional warning)
            ENERGY_BUDGET_WH = 500  # 500Wh/ngày = 0.5kWh
            if self.energy_today_wh > ENERGY_BUDGET_WH:
                # Vượt ngân sách → log warning (không tắt thiết bị, chỉ cảnh báo)
                elapsed_warn = time.ticks_diff(time.ticks_ms(), self.last_anomaly_log)
                if elapsed_warn > 300000:  # log mỗi 5 phút
                    self._log_decision("ENERGY BUDGET EXCEEDED", "WARNING", {"today_wh": self.energy_today_wh, "budget": ENERGY_BUDGET_WH})
                    self.last_anomaly_log = time.ticks_ms()
        except Exception as e:
            print(f"Loi Energy Budget: {e}")
        
        # --- 6. Publish Growth State Attributes ---
        try:
            # Publish growth state mỗi 30 giây (tránh spam)
            now_ms = time.ticks_ms()
            if not hasattr(self, '_last_growth_state_publish'):
                self._last_growth_state_publish = 0
            
            elapsed_ms = time.ticks_diff(now_ms, self._last_growth_state_publish)
            if elapsed_ms >= 30000:  # 30 seconds
                growth_attrs = {
                    "growth_week": self.growth_week,
                    "days_since_planting": self.growth_state.get_days_since_planting(),
                    "plant_start_date": self.growth_state.get_plant_start_date()
                }
                
                # Lấy tên stage hiện tại nếu có
                current_stage = self._get_current_growth_stage_name()
                if current_stage:
                    growth_attrs["current_growth_stage"] = current_stage
                
                # Push attributes lên cloud
                if hasattr(self.ai, 'set_snapshot'):
                    self.ai.set_snapshot(growth_attrs, immediate=False)
                
                self._last_growth_state_publish = now_ms
        except Exception as e:
            print(f"Loi Growth State Publish: {e}")