# i2c_lcd.py

from lcd_api import LcdApi
from machine import I2C
import time

class I2cLcd(LcdApi):
    MASK_RS = 0x01
    MASK_RW = 0x02
    MASK_E  = 0x04
    MASK_BACKLIGHT = 0x08  # Bit đèn nền

    def __init__(self, i2c, i2c_addr, num_lines, num_columns):
        self.i2c = i2c
        self.i2c_addr = i2c_addr
        self.backlight = self.MASK_BACKLIGHT  # Luôn bật đèn nền
        time.sleep_ms(20)
        self.i2c.writeto(self.i2c_addr, bytearray([0x00 | self.backlight]))
        super().__init__(num_lines, num_columns)

    def hal_write_init_nibble(self, nibble):
        self.hal_write_byte(nibble << 4)

    def hal_backlight_on(self):
        self.backlight = self.MASK_BACKLIGHT

    def hal_backlight_off(self):
        self.backlight = 0

    def hal_write_command(self, cmd):
        self.hal_write_byte(cmd & 0xF0)
        self.hal_write_byte((cmd << 4) & 0xF0)

    def hal_write_data(self, data):
        self.hal_write_byte((data & 0xF0), True)
        self.hal_write_byte(((data << 4) & 0xF0), True)

    def hal_write_byte(self, nibble, rs=False):
        data = nibble
        if rs:
            data |= self.MASK_RS
        data |= self.backlight
        self.i2c.writeto(self.i2c_addr, bytearray([data | self.MASK_E]))
        time.sleep_us(1)
        self.i2c.writeto(self.i2c_addr, bytearray([data & ~self.MASK_E]))
        time.sleep_us(50)
