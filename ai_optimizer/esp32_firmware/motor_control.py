# motor_control.py - Điều khiển quạt bằng PWM
# Tốc độ 0-100% (duty cycle)

from machine import Pin, PWM

class MotorController:
    def __init__(self, pin=18, freq=1000):
        self.pwm = PWM(Pin(pin))
        self.pwm.freq(freq)
        self.current_duty_percent = 0
        self.set_speed(0)

    def set_speed(self, duty_percent):
        """0..100 (%)"""
        try:
            duty_percent = max(0, min(100, duty_percent))
            duty = int((duty_percent / 100) * 65535)
            self.pwm.duty_u16(duty)
            self.current_duty_percent = duty_percent
            print(f"Động cơ: Tốc độ {duty_percent}%")
            return True
        except Exception as e:
            print(f"Lỗi khi thiết lập tốc độ động cơ: {e}")
            return False

    def stop(self):
        return self.set_speed(0)

    def get_speed(self):
        try:
            return {'status': True, 'speed': self.current_duty_percent}
        except Exception as e:
            print(f"Lỗi khi lấy trạng thái tốc độ động cơ: {e}")
            return {'status': False, 'speed': None}
