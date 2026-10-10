# quickunzip/dnd.py — 用 ctypes 让 tkinter 顶层窗口接收资源管理器拖入的文件 / 文件夹（WM_DROPFILES，仅 Windows，无第三方依赖）
#
# 用法：from quickunzip import dnd；dnd.enable(root, on_drop)，on_drop(路径列表) 在 Tk 线程中回调
# 配套文件：quickunzip/gui_main.py

import os

WM_DROPFILES = 0x0233
GWLP_WNDPROC = -4
POLL_MS = 100

if os.name == 'nt':
    import ctypes
    from ctypes import wintypes

    _user32 = ctypes.windll.user32
    _shell32 = ctypes.windll.shell32
    LRESULT = ctypes.c_ssize_t
    WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wintypes.HWND, wintypes.UINT,
                                 wintypes.WPARAM, wintypes.LPARAM)

    _SetWindowLongPtr = getattr(_user32, 'SetWindowLongPtrW', None) or _user32.SetWindowLongW
    _SetWindowLongPtr.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_void_p]
    _SetWindowLongPtr.restype = ctypes.c_void_p
    _user32.CallWindowProcW.argtypes = [ctypes.c_void_p, wintypes.HWND, wintypes.UINT,
                                        wintypes.WPARAM, wintypes.LPARAM]
    _user32.CallWindowProcW.restype = LRESULT
    _user32.GetParent.argtypes = [wintypes.HWND]
    _user32.GetParent.restype = wintypes.HWND
    _shell32.DragAcceptFiles.argtypes = [wintypes.HWND, wintypes.BOOL]
    _shell32.DragQueryFileW.argtypes = [ctypes.c_void_p, wintypes.UINT,
                                        wintypes.LPWSTR, wintypes.UINT]
    _shell32.DragQueryFileW.restype = wintypes.UINT
    _shell32.DragFinish.argtypes = [ctypes.c_void_p]


def toplevel_hwnd(window):
    """Tk 顶层窗口对应的 Win32 窗口句柄(winfo_id 是内部客户区,其父窗口才是外框)。"""
    window.update_idletasks()
    return _user32.GetParent(window.winfo_id()) or window.winfo_id()


def _read_hdrop(hdrop):
    count = _shell32.DragQueryFileW(hdrop, 0xFFFFFFFF, None, 0)
    files = []
    for i in range(count):
        length = _shell32.DragQueryFileW(hdrop, i, None, 0)
        buf = ctypes.create_unicode_buffer(length + 1)
        _shell32.DragQueryFileW(hdrop, i, buf, length + 1)
        files.append(buf.value)
    _shell32.DragFinish(hdrop)
    return files


def enable(window, on_drop):
    """让 Tk 顶层窗口 window 接收拖放;每次拖入调用 on_drop(路径列表)。返回是否启用成功。"""
    if os.name != 'nt':
        return False
    hwnd = toplevel_hwnd(window)
    pending = []

    def wndproc(h, msg, wparam, lparam):
        if msg == WM_DROPFILES:
            pending.append(_read_hdrop(wparam))
            return 0
        return _user32.CallWindowProcW(old_proc, h, msg, wparam, lparam)

    proc = WNDPROC(wndproc)
    old_proc = _SetWindowLongPtr(hwnd, GWLP_WNDPROC, ctypes.cast(proc, ctypes.c_void_p))
    _shell32.DragAcceptFiles(hwnd, True)
    # 回调对象必须一直被引用,否则被回收后窗口过程会指向野指针
    window._quickunzip_dnd = proc

    def poll():
        while pending:
            on_drop(pending.pop(0))
        window.after(POLL_MS, poll)

    window.after(POLL_MS, poll)
    return True
