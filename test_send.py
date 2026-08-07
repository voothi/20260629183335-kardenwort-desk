import ctypes
from ctypes import wintypes

def send_ahk_message(window_title_substring, args_list):
    HWND = wintypes.HWND
    DWORD = wintypes.DWORD
    ULONG_PTR = wintypes.WPARAM
    
    class COPYDATASTRUCT(ctypes.Structure):
        _fields_ = [
            ('dwData', ULONG_PTR),
            ('cbData', DWORD),
            ('lpData', ctypes.c_void_p)
        ]
        
    user32 = ctypes.windll.user32
    
    hwnd_target = 0
    def enum_windows_proc(hwnd, lParam):
        nonlocal hwnd_target
        length = user32.GetWindowTextLengthW(hwnd)
        if length > 0:
            buff = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, buff, length + 1)
            if window_title_substring in buff.value and "AutoHotkey" in buff.value:
                hwnd_target = hwnd
                return False
        return True
    
    EnumWindowsProc = ctypes.WINFUNCTYPE(wintypes.BOOL, HWND, wintypes.LPARAM)
    user32.EnumWindows(EnumWindowsProc(enum_windows_proc), 0)
    
    if not hwnd_target:
        print("Could not find AHK window")
        return False
        
    payload_string = "\n".join(args_list) + "\n"
    encoded_str = payload_string.encode('utf-16le') + b'\x00\x00'
    buffer = ctypes.create_string_buffer(encoded_str)
    
    cds = COPYDATASTRUCT()
    cds.dwData = 1
    cds.cbData = len(encoded_str)
    cds.lpData = ctypes.cast(buffer, ctypes.c_void_p)
    
    WM_COPYDATA = 0x004A
    user32.SendMessageW(hwnd_target, WM_COPYDATA, 0, ctypes.byref(cds))
    print(f"Sent message to {hwnd_target}")
    return True

send_ahk_message("kardenwort-window.ahk", ["--seq-num", "1", "--restore", r"U:\voothi\20260629183335-kardenwort-desk\results\20260807190100-this-is-a-wellknown.en.tsv"])
