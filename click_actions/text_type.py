"""
通过 Windows SendInput 发送 Unicode 文本（支持中文），需在已聚焦的输入框内使用。
"""
import ctypes
import time

INPUT_KEYBOARD = 1
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_UNICODE = 0x0004


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", ctypes.c_long),
        ("dy", ctypes.c_long),
        ("mouseData", ctypes.c_uint),
        ("dwFlags", ctypes.c_uint),
        ("time", ctypes.c_uint),
        ("dwExtraInfo", ctypes.c_size_t),
    ]


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", ctypes.c_ushort),
        ("wScan", ctypes.c_ushort),
        ("dwFlags", ctypes.c_uint),
        ("time", ctypes.c_uint),
        ("dwExtraInfo", ctypes.c_size_t),
    ]


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [
        ("uMsg", ctypes.c_uint),
        ("wParamL", ctypes.c_ushort),
        ("wParamH", ctypes.c_ushort),
    ]


class _INPUTUNION(ctypes.Union):
    _fields_ = [
        ("mi", MOUSEINPUT),
        ("ki", KEYBDINPUT),
        ("hi", HARDWAREINPUT),
    ]


class INPUT(ctypes.Structure):
    _fields_ = [("type", ctypes.c_uint), ("u", _INPUTUNION)]


def _send_input_pair(user32, scan: int) -> None:
    down = INPUT(
        type=INPUT_KEYBOARD,
        u=_INPUTUNION(ki=KEYBDINPUT(0, scan, KEYEVENTF_UNICODE, 0, 0)),
    )
    up = INPUT(
        type=INPUT_KEYBOARD,
        u=_INPUTUNION(ki=KEYBDINPUT(0, scan, KEYEVENTF_UNICODE | KEYEVENTF_KEYUP, 0, 0)),
    )
    arr = (INPUT * 2)(down, up)
    sent = user32.SendInput(2, arr, ctypes.sizeof(INPUT))
    if sent != 2:
        err = ctypes.GetLastError()
        raise RuntimeError(f"SendInput Unicode 失败: 返回 {sent}, last_error={err}")


def _send_vk_pair(user32, vk: int) -> None:
    down = INPUT(
        type=INPUT_KEYBOARD,
        u=_INPUTUNION(ki=KEYBDINPUT(vk, 0, 0, 0, 0)),
    )
    up = INPUT(
        type=INPUT_KEYBOARD,
        u=_INPUTUNION(ki=KEYBDINPUT(vk, 0, KEYEVENTF_KEYUP, 0, 0)),
    )
    arr = (INPUT * 2)(down, up)
    sent = user32.SendInput(2, arr, ctypes.sizeof(INPUT))
    if sent != 2:
        err = ctypes.GetLastError()
        raise RuntimeError(f"SendInput 按键失败: vk={vk} 返回 {sent}, last_error={err}")


def type_unicode_text(
    text: str,
    delay_sec: float = 0.0,
    interval_sec: float = 0.0,
    press_enter: bool = False,
) -> None:
    if delay_sec > 0:
        time.sleep(delay_sec)
    user32 = ctypes.windll.user32
    for ch in text:
        _send_input_pair(user32, ord(ch))
        if interval_sec > 0:
            time.sleep(interval_sec)
    if press_enter:
        _send_vk_pair(user32, 0x0D)  # Enter


class TextTypeService:
    def type_text(
        self,
        content: str,
        delay_sec: float = 0.0,
        interval_sec: float = 0.0,
        press_enter: bool = False,
    ) -> None:
        type_unicode_text(
            text=content,
            delay_sec=delay_sec,
            interval_sec=interval_sec,
            press_enter=press_enter,
        )
