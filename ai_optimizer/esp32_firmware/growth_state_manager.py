# growth_state_manager.py - Quản lý trạng thái chu kỳ phát triển cây trồng
# Lưu persistent vào file JSON để tránh mất dữ liệu khi reboot/mất điện

import time
try:
    import ujson as json
except ImportError:
    import json

class GrowthStateManager:
    """Quản lý persistent state của chu kỳ phát triển cây trồng."""
    
    def __init__(self, state_file="growth_state.json"):
        self.state_file = state_file
        self.state = {
            "plant_start_date": 0,
            "profile_name": "",
            "profile_changed_date": 0,
            "last_saved": 0
        }
        self._load_state()
    
    def _load_state(self):
        try:
            with open(self.state_file, 'r') as f:
                loaded = json.load(f)
                self.state.update(loaded)
            print(f"[GrowthState] Loaded from {self.state_file}")
            return True
        except OSError:
            print(f"[GrowthState] File not found, creating new state")
            self._initialize_new_cycle()
            return False
        except Exception as e:
            print(f"[GrowthState] Error loading state: {e}")
            self._initialize_new_cycle()
            return False
    
    def _save_state(self):
        try:
            self.state["last_saved"] = time.time()
            with open(self.state_file, 'w') as f:
                json.dump(self.state, f)
            return True
        except Exception as e:
            print(f"[GrowthState] Error saving state: {e}")
            return False
    
    def _initialize_new_cycle(self):
        now = time.time()
        self.state = {
            "plant_start_date": now,
            "profile_name": "unknown",
            "profile_changed_date": now,
            "last_saved": now
        }
        self._save_state()
    
    def get_plant_start_date(self):
        return self.state.get("plant_start_date", 0)
    
    def get_growth_week(self):
        start_date = self.state.get("plant_start_date", 0)
        if start_date == 0:
            return 1
        try:
            elapsed_days = (time.time() - start_date) / 86400.0
            week = int(elapsed_days / 7) + 1
            return max(1, week)
        except Exception:
            return 1
    
    def get_days_since_planting(self):
        start_date = self.state.get("plant_start_date", 0)
        if start_date == 0:
            return 0
        try:
            return max(0, (time.time() - start_date) / 86400.0)
        except Exception:
            return 0
    
    def update_profile(self, profile_name, reset_cycle=False):
        old_profile = self.state.get("profile_name", "")
        now = time.time()
        if reset_cycle:
            self.state["plant_start_date"] = now
        self.state["profile_name"] = profile_name
        self.state["profile_changed_date"] = now
        self._save_state()
    
    def reset_plant_cycle(self, new_start_date=None):
        if new_start_date is None:
            new_start_date = time.time()
        self.state["plant_start_date"] = new_start_date
        self.state["profile_changed_date"] = new_start_date
        self._save_state()
    
    def set_plant_start_date(self, timestamp):
        self.state["plant_start_date"] = timestamp
        self._save_state()
    
    def get_state_summary(self):
        return {
            "profile_name": self.state.get("profile_name", "unknown"),
            "plant_start_date": self.state.get("plant_start_date", 0),
            "days_since_planting": self.get_days_since_planting(),
            "growth_week": self.get_growth_week(),
            "profile_changed_date": self.state.get("profile_changed_date", 0),
            "last_saved": self.state.get("last_saved", 0)
        }
