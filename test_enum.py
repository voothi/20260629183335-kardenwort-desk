import ctypes
from ctypes import wintypes
user32 = ctypes.windll.user32
titles = []
def proc(hwnd, lParam):
    length = user32.GetWindowTextLengthW(hwnd)
    if length > 0:
        buff = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buff, length + 1)
        if 'kardenwort-window.ahk' in buff.value:
            titles.append(buff.value)
    return True
EnumWindowsProc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
user32.EnumWindows(EnumWindowsProc(proc), 0)
print(titles)
