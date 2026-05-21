import machine

# usb_switch_controller.py - Điều khiển 2 kênh USB switch (relay/MOSFET)
# CH1: đèn LED
# CH2: bơm nước

class USBSwitchController:
    def __init__(self, ch1_pin=10, ch2_pin=17, invert_logic=False):
        self.invert_logic = invert_logic
        try:
            self.pin1 = machine.Pin(ch1_pin, machine.Pin.OUT)
            self.pin2 = machine.Pin(ch2_pin, machine.Pin.OUT)
        except Exception as e:
            try:
                self.pin1 = machine.Pin(10, machine.Pin.OUT)
                self.pin2 = machine.Pin(17, machine.Pin.OUT)
            except Exception:
                raise e
        self.control_switch(1, 0)
        self.control_switch(2, 0)

    def control_switch(self, channel, state):
        """channel: 1|2, state: 1=ON, 0=OFF"""
        try:
            output = 1 - state if self.invert_logic else state
            if channel == 1:
                self.pin1.value(output)
                print(f"Kênh 1 (USB Out 1): {'ON' if state else 'OFF'}")
                return True
            elif channel == 2:
                self.pin2.value(output)
                print(f"Kênh 2 (USB Out 2): {'ON' if state else 'OFF'}")
                return True
            else:
                print(f"Lỗi: Kênh {channel} không hợp lệ")
                return False
        except Exception as e:
            print(f"Lỗi điều khiển switch kênh {channel}: {e}")
            return False

    def toggle_switch(self, channel):
        try:
            if channel == 1:
                current_raw = self.pin1.value()
                new_raw = 0 if current_raw else 1
                new_state = 1 - new_raw if self.invert_logic else new_raw
                return self.control_switch(1, new_state)
            elif channel == 2:
                current_raw = self.pin2.value()
                new_raw = 0 if current_raw else 1
                new_state = 1 - new_raw if self.invert_logic else new_raw
                return self.control_switch(2, new_state)
            else:
                print(f"Lỗi: Kênh {channel} không hợp lệ")
                return False
        except Exception as e:
            print(f"Lỗi khi toggle kênh {channel}: {e}")
            return False

    def get_switch_state(self, channel):
        try:
            if channel == 1:
                raw = self.pin1.value()
                effective = 1 - raw if self.invert_logic else raw
                return {'status': True, 'state': 'ON' if effective else 'OFF'}
            elif channel == 2:
                raw = self.pin2.value()
                effective = 1 - raw if self.invert_logic else raw
                return {'status': True, 'state': 'ON' if effective else 'OFF'}
            else:
                print(f"Lỗi: Kênh {channel} không hợp lệ")
                return {'status': False, 'state': None}
        except Exception as e:
            print(f"Lỗi khi lấy trạng thái kênh {channel}: {e}")
            return {'status': False, 'state': None}

    def get_all_states(self):
        s1 = self.get_switch_state(1)
        s2 = self.get_switch_state(2)
        return {
            'channel1': s1['state'] if s1['status'] else None,
            'channel2': s2['state'] if s2['status'] else None
        }
