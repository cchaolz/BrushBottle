import win32gui
import win32con
import win32api
import time
import ctypes

import win32gui
import win32api
import win32con
import ctypes
import time

def send_input_click(hwnd, x, y):
    # 获取当前鼠标位置
    current_x, current_y = win32api.GetCursorPos()

    # 获取窗口左上角的屏幕坐标
    left, top, right, bottom = win32gui.GetWindowRect(hwnd)
    # 计算目标点的屏幕坐标
    screen_x = left + x
    screen_y = top + y

    # 移动鼠标到目标点
    win32api.SetCursorPos((screen_x, screen_y))
    time.sleep(0.01)  # 稍作延迟

    # 构造输入结构体
    class MOUSEINPUT(ctypes.Structure):
        _fields_ = [("dx", ctypes.c_long),
                    ("dy", ctypes.c_long),
                    ("mouseData", ctypes.c_ulong),
                    ("dwFlags", ctypes.c_ulong),
                    ("time", ctypes.c_ulong),
                    ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong))]

    class INPUT(ctypes.Structure):
        _fields_ = [("type", ctypes.c_ulong),
                    ("mi", MOUSEINPUT)]

    # 鼠标按下
    mi_down = MOUSEINPUT(0, 0, 0, win32con.MOUSEEVENTF_LEFTDOWN, 0, None)
    input_down = INPUT(win32con.INPUT_MOUSE, mi_down)
    ctypes.windll.user32.SendInput(1, ctypes.byref(input_down), ctypes.sizeof(input_down))

    time.sleep(0.01)

    # 鼠标抬起
    mi_up = MOUSEINPUT(0, 0, 0, win32con.MOUSEEVENTF_LEFTUP, 0, None)
    input_up = INPUT(win32con.INPUT_MOUSE, mi_up)
    ctypes.windll.user32.SendInput(1, ctypes.byref(input_up), ctypes.sizeof(input_up))

    time.sleep(0.03)
    # 将鼠标移回原位
    win32api.SetCursorPos((current_x, current_y))

def send_input_rclick(hwnd):
    # 获取当前鼠标位置
    current_x, current_y = win32api.GetCursorPos()

    # 获取窗口左上角的屏幕坐标
    left, top, right, bottom = win32gui.GetWindowRect(hwnd)
    # 计算目标点的屏幕坐标
    screen_x = (left + right) // 2
    screen_y = (top + bottom) // 2

    # 移动鼠标到目标点
    win32api.SetCursorPos((screen_x, screen_y))
    time.sleep(0.01)  # 稍作延迟

    # 构造输入结构体
    class MOUSEINPUT(ctypes.Structure):
        _fields_ = [("dx", ctypes.c_long),
                    ("dy", ctypes.c_long),
                    ("mouseData", ctypes.c_ulong),
                    ("dwFlags", ctypes.c_ulong),
                    ("time", ctypes.c_ulong),
                    ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong))]

    class INPUT(ctypes.Structure):
        _fields_ = [("type", ctypes.c_ulong),
                    ("mi", MOUSEINPUT)]

    # 鼠标按下
    mi_down = MOUSEINPUT(0, 0, 0, win32con.MOUSEEVENTF_RIGHTDOWN, 0, None)
    input_down = INPUT(win32con.INPUT_MOUSE, mi_down)
    ctypes.windll.user32.SendInput(1, ctypes.byref(input_down), ctypes.sizeof(input_down))

    time.sleep(0.01)

    # 鼠标抬起
    mi_up = MOUSEINPUT(0, 0, 0, win32con.MOUSEEVENTF_RIGHTUP, 0, None)
    input_up = INPUT(win32con.INPUT_MOUSE, mi_up)
    ctypes.windll.user32.SendInput(1, ctypes.byref(input_up), ctypes.sizeof(input_up))

    time.sleep(0.03)
    # 将鼠标移回原位
    win32api.SetCursorPos((current_x, current_y))

