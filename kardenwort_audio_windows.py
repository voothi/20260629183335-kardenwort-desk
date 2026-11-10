"""Resume an owned suspended Windows launcher after assigning its process job."""
import ctypes
from ctypes import wintypes


class ThreadEntry(ctypes.Structure):
    _fields_ = [('size',wintypes.DWORD),('usage',wintypes.DWORD),('thread',wintypes.DWORD),
        ('process',wintypes.DWORD),('base',wintypes.LONG),('delta',wintypes.LONG),('flags',wintypes.DWORD)]


def thread_api():
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD,wintypes.DWORD]
    kernel.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    kernel.Thread32First.argtypes = [wintypes.HANDLE,ctypes.POINTER(ThreadEntry)]
    kernel.Thread32Next.argtypes = [wintypes.HANDLE,ctypes.POINTER(ThreadEntry)]
    kernel.OpenThread.argtypes = [wintypes.DWORD,wintypes.BOOL,wintypes.DWORD]
    kernel.OpenThread.restype = wintypes.HANDLE
    kernel.ResumeThread.argtypes = [wintypes.HANDLE]
    kernel.ResumeThread.restype = wintypes.DWORD
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    return kernel


def primary_thread(kernel, pid):
    snapshot = kernel.CreateToolhelp32Snapshot(4,0)
    if snapshot == ctypes.c_void_p(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        entry = ThreadEntry()
        entry.size = ctypes.sizeof(entry)
        found = kernel.Thread32First(snapshot, ctypes.byref(entry))
        while found:
            if entry.process == pid:
                return entry.thread
            found = kernel.Thread32Next(snapshot, ctypes.byref(entry))
        raise RuntimeError('Owned suspended audio process has no primary thread')
    finally:
        kernel.CloseHandle(snapshot)


def resume_owned_process(pid):
    kernel = thread_api()
    handle = kernel.OpenThread(2,False,primary_thread(kernel,pid))
    if not handle:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        if kernel.ResumeThread(handle) != 1:
            raise RuntimeError('Owned audio thread did not resume from its initial suspension')
    finally:
        kernel.CloseHandle(handle)
