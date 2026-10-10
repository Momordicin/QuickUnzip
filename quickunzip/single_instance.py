# quickunzip/single_instance.py — 单实例合并：多选右键时每个选中项各启动一个进程，第一个进程当服务端，其余把路径通过命名管道交给它后退出
#
# 用法：from quickunzip import single_instance；server = single_instance.deliver_or_serve('here', ('paths', [...]), inbox.put)；server 为 None 表示已交给已运行的实例；退出前 server.close()
# 配套文件：quickunzip/gui_progress.py / quickunzip/gui_main.py / tests/test_single_instance.py

import os
import threading
import time
from multiprocessing.connection import AuthenticationError, Client, Listener

AUTHKEY = b'QuickUnzip'
ERROR_ALREADY_EXISTS = 183
ASFW_ANY = 0xFFFFFFFF
_QUIT = ('__quit__',)


def _names(channel):
    user = ''.join(c for c in os.environ.get('USERNAME', '') if c.isalnum()) or 'user'
    base = f'QuickUnzip-{user}-{channel}'
    return rf'\\.\pipe\{base}', rf'Local\{base}'


def _kernel32():
    import ctypes
    k32 = ctypes.WinDLL('kernel32', use_last_error=True)
    k32.CreateMutexW.restype = ctypes.c_void_p
    k32.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_bool, ctypes.c_wchar_p]
    k32.CloseHandle.argtypes = [ctypes.c_void_p]
    return ctypes, k32


def _acquire_mutex(name):
    ctypes, k32 = _kernel32()
    handle = k32.CreateMutexW(None, False, name)
    if not handle:
        return None
    if ctypes.get_last_error() == ERROR_ALREADY_EXISTS:
        k32.CloseHandle(handle)
        return None
    return handle


def _release_mutex(handle):
    if handle:
        _kernel32()[1].CloseHandle(handle)


def _allow_foreground():
    """让收到消息的已运行实例可以把自己的窗口切到前台。"""
    try:
        import ctypes
        ctypes.windll.user32.AllowSetForegroundWindow(ASFW_ANY)
    except (AttributeError, OSError):
        pass


def send(address, message):
    try:
        conn = Client(address, family='AF_PIPE', authkey=AUTHKEY)
    except (OSError, EOFError, AuthenticationError):
        return False
    try:
        conn.send(message)
    except (OSError, EOFError):
        return False
    finally:
        conn.close()
    return True


class Server:
    """持有互斥量并在后台线程接收消息;on_message 在接收线程里调用。"""

    def __init__(self, address, mutex, on_message):
        self.address = address
        self.mutex = mutex
        self.on_message = on_message
        self.closed = False
        self.listener = Listener(address, family='AF_PIPE', authkey=AUTHKEY)
        self.thread = threading.Thread(target=self._loop, daemon=True)
        self.thread.start()

    def _loop(self):
        while not self.closed:
            try:
                conn = self.listener.accept()
            except (OSError, EOFError, AuthenticationError):
                continue
            try:
                message = conn.recv()
            except (OSError, EOFError):
                continue
            finally:
                conn.close()
            if message == _QUIT:
                return
            self.on_message(message)

    def close(self):
        """停止接收;返回后不会再调用 on_message。之后新启动的进程会成为新的服务端。"""
        if self.closed:
            return
        self.closed = True
        send(self.address, _QUIT)
        self.thread.join(2)
        self.listener.close()
        _release_mutex(self.mutex)
        self.mutex = None


class Standalone:
    """拿不到单实例(超时或非 Windows)时的占位:照常独立运行。"""

    def close(self):
        pass


def deliver_or_serve(channel, message, on_message, timeout=10):
    """把 message 交给已运行的实例并返回 None;没有实例时自己成为服务端并返回 Server。

    返回 Server 时,message 不会经由 on_message 回调,调用方自己处理。
    """
    if os.name != 'nt':
        return Standalone()
    address, mutex_name = _names(channel)
    deadline = time.monotonic() + timeout
    _allow_foreground()
    while True:
        if send(address, message):
            return None
        mutex = _acquire_mutex(mutex_name)
        if mutex:
            try:
                return Server(address, mutex, on_message)
            except OSError:
                _release_mutex(mutex)
        if time.monotonic() > deadline:
            return Standalone()
        time.sleep(0.05)
