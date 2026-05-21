import time
from machine import Pin, I2C
try:
    from i2c_lcd import I2cLcd
except ImportError as e:
    raise ImportError(f"Lỗi import I2cLcd: {e}")

class LCDController:
    def __init__(self, scl_pin=12, sda_pin=11, i2c_addr=0x21, rows=2, cols=16):
        self.i2c = I2C(0, scl=Pin(scl_pin), sda=Pin(sda_pin))
        self.lcd = I2cLcd(self.i2c, i2c_addr, rows, cols)
        self.rows = rows
        self.cols = cols
        self.last_text = [""] * rows
        self.backlight_on = False
        self.clear()
        self.set_backlight(True)

    def set_backlight(self, state):
        try:
            if state:
                self.lcd.hal_backlight_on()
                self.backlight_on = True
            else:
                self.lcd.hal_backlight_off()
                self.backlight_on = False
            return True
        except Exception as e:
            print(f"Lỗi đèn nền: {e}")
            return False

    def display_text(self, text, row, col=0):
        try:
            if not 0 <= row < self.rows:
                return False
            if not 0 <= col < self.cols:
                return False
            s = str(text)[:self.cols - col]
            self.lcd.move_to(0, row)
            self.lcd.putstr(" " * self.cols)
            self.lcd.move_to(col, row)
            self.lcd.putstr(s)
            pad = " " * max(0, self.cols - col - len(s))
            self.last_text[row] = (self.last_text[row][:col] + s + pad)[:self.cols]
            return True
        except Exception as e:
            print(f"Lỗi hiển thị: {e}")
            return False

    def clear(self):
        try:
            self.lcd.clear()
            self.last_text = [""] * self.rows
            return True
        except Exception as e:
            print(f"Lỗi xóa màn hình: {e}")
            return False

    def get_display_state(self):
        return {'status': True, 'backlight': self.backlight_on, 'text': self.last_text.copy()}
