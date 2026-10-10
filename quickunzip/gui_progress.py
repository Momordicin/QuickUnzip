# quickunzip/gui_progress.py — 右键"智能解压到此处" / 拖到图标上时的进度小窗口：多次触发合并排队，全部成功自动关闭，有失败停住
#
# 用法：from quickunzip import gui_progress；gui_progress.run(paths)
# 配套文件：quickunzip/single_instance.py / quickunzip/core.py / quickunzip/shell_menu.py / quickunzip/gui_main.py / jieya.py

import os
import queue
import threading
import time
import tkinter as tk
from tkinter import ttk

from . import core, paths, shell_menu, single_instance
from .config import Config, UserError
from .gui_main import APP_TITLE, LinkLabel, open_path, set_dpi_aware, _short

POLL_MS = 100
DEBOUNCE_S = 0.4
AUTO_CLOSE_MS = 1200


class ProgressWindow:
    def __init__(self, root, server, inbox, initial_paths):
        self.root = root
        self.server = server
        self.inbox = inbox
        self.pending = list(initial_paths)
        self.last_arrival = time.monotonic()
        self.events = queue.Queue()
        self.worker = None
        self.stop_event = threading.Event()
        self.ran = not self.pending
        self.user_closed = False
        self.total = self.done = self.ok = self.failed = 0
        self.kept_no_recycle_bin = 0
        self.log_write_failed = False
        self.batch_offset = 0
        self.stopped = False
        self.error = None
        self.closing = False
        self.auto_close_id = None

        root.title(f'{APP_TITLE} - 智能解压')
        if paths.ICON_FILE:
            try:
                root.iconbitmap(default=paths.ICON_FILE)
            except tk.TclError:
                pass
        root.resizable(False, False)
        root.protocol('WM_DELETE_WINDOW', self.on_close)
        style = ttk.Style(root)
        if 'vista' in style.theme_names():
            style.theme_use('vista')
        style.configure('Link.TLabel', foreground='#0b62c4', font=('Microsoft YaHei UI', 9, 'underline'))
        style.configure('Counts.TLabel', font=('Microsoft YaHei UI', 10, 'bold'))

        body = ttk.Frame(root, padding=14)
        body.pack(fill='both', expand=True)
        self.status_var = tk.StringVar(value='正在准备…')
        ttk.Label(body, textvariable=self.status_var, width=52).pack(anchor='w')
        row = ttk.Frame(body)
        row.pack(fill='x', pady=(8, 0))
        self.counts_var = tk.StringVar(value='成功 0 个 / 失败 0 个')
        ttk.Label(row, textvariable=self.counts_var, style='Counts.TLabel').pack(side='left')
        self.link_log = LinkLabel(row, '查看失败日志', lambda: open_path(paths.FAIL_LOG_FILE))
        buttons = ttk.Frame(body)
        buttons.pack(fill='x', pady=(12, 0))
        self.btn_close = ttk.Button(buttons, text='关闭', command=self.on_close)
        self.btn_stop = ttk.Button(buttons, text='停止', command=self.stop)
        self.btn_close.pack(side='right')
        self.btn_stop.pack(side='right', padx=(0, 6))

        root.update_idletasks()
        w, h = root.winfo_reqwidth(), root.winfo_reqheight()
        x = (root.winfo_screenwidth() - w) // 2
        y = (root.winfo_screenheight() - h) // 3
        root.geometry(f'+{x}+{y}')
        root.after(POLL_MS, self._poll)

    @property
    def busy(self):
        return self.worker is not None and self.worker.is_alive()

    def _poll(self):
        while True:
            try:
                message = self.inbox.get_nowait()
            except queue.Empty:
                break
            self._receive(message)
        while True:
            try:
                event = self.events.get_nowait()
            except queue.Empty:
                break
            self._handle(event)
        if not self.busy:
            if self.pending and not self.closing:
                if time.monotonic() - self.last_arrival >= DEBOUNCE_S:
                    self._start_batch()
            elif self.closing:
                self._shutdown()
                return
            elif self.ran:
                self._idle()
        self.root.after(POLL_MS, self._poll)

    def _receive(self, message):
        if message and message[0] == 'paths' and message[1]:
            self.pending += message[1]
            self.last_arrival = time.monotonic()
            if self.auto_close_id:
                self.root.after_cancel(self.auto_close_id)
                self.auto_close_id = None

    def _start_batch(self):
        batch, self.pending = self.pending, []
        self.stopped = False
        self.error = None
        self.ran = True
        self.batch_offset = self.done
        self.stop_event.clear()
        self.btn_stop.state(['!disabled'])
        self.status_var.set('正在准备…')
        self.worker = threading.Thread(target=self._work, args=(batch,), daemon=True)
        self.worker.start()

    def _work(self, batch):
        try:
            cfg = Config.load()
            jobs = core.plan_in_place(batch)
            self.events.put(('planned', len(jobs)))
            if not jobs:
                return

            def progress(done, _total, current, result):
                self.events.put(('progress', done, current, result.succeeded, result.failed))

            result = core.Extractor.from_config(cfg, on_progress=progress,
                                                stop_event=self.stop_event).run(jobs)
            self.events.put(('done', result))
        except UserError as e:
            self.events.put(('error', str(e)))
        except Exception as e:
            self.events.put(('error', f"{type(e).__name__}: {e}"))

    def _handle(self, event):
        kind = event[0]
        if kind == 'planned':
            self.total += event[1]
        elif kind == 'progress':
            _, done, current, ok, failed = event
            self.counts_var.set(f"成功 {self.ok + ok} 个 / 失败 {self.failed + failed} 个")
            if current is not None and not self.stop_event.is_set():
                self.status_var.set(
                    f"[{self.batch_offset + done + 1}/{self.total}] 正在解压: {_short(current, 40)}")
        elif kind == 'done':
            result = event[1]
            self.done += result.succeeded + result.failed
            self.ok += result.succeeded
            self.failed += result.failed
            self.kept_no_recycle_bin += result.kept_no_recycle_bin
            if result.failures and not result.log_file:
                self.log_write_failed = True
            self.stopped = self.stopped or result.stopped
            self.counts_var.set(f"成功 {self.ok} 个 / 失败 {self.failed} 个")
        elif kind == 'error':
            self.error = event[1]

    def _idle(self):
        self.btn_stop.state(['disabled'])
        if self.error:
            self.status_var.set(f"出错: {_short(self.error, 60)}")
        elif self.stopped:
            self.status_var.set(f"已停止, 未处理 {self.total - self.done} 个")
        elif self.total == 0:
            self.status_var.set('没有可解压的文件')
        elif self.failed:
            self.status_var.set(f"完成, 共 {self.total} 个")
        elif self.kept_no_recycle_bin:
            self.status_var.set(f"完成, 共 {self.total} 个; {self.kept_no_recycle_bin} 个源文件"
                                f"所在位置没有回收站, 未删除")
        else:
            self.status_var.set(f"全部完成, 共 {self.total} 个")
            if not self.auto_close_id:
                self.auto_close_id = self.root.after(AUTO_CLOSE_MS, self._auto_close)
            return
        if self.failed and self.log_write_failed:
            self.status_var.set(f"{self.status_var.get()}  (失败日志写入失败)")
        elif self.failed and not self.link_log.winfo_ismapped():
            self.link_log.pack(side='left', padx=(12, 0))

    def _auto_close(self):
        self.auto_close_id = None
        if not self.busy and not self.pending:
            self.closing = True

    def stop(self):
        if self.busy:
            self.stop_event.set()
            self.pending = []
            self.btn_stop.state(['disabled'])
            self.status_var.set('正在停止… 当前压缩包处理完后停止')

    def on_close(self):
        self.user_closed = True
        self.closing = True
        self.stop()

    def _shutdown(self):
        self.server.close()
        while True:
            try:
                self._receive(self.inbox.get_nowait())
            except queue.Empty:
                break
        if self.pending and not self.user_closed:
            self.closing = False
            self.root.after(POLL_MS, self._poll)
            return
        self.root.destroy()


def run(initial_paths):
    inbox = queue.Queue()
    server = single_instance.deliver_or_serve('here', ('paths', list(initial_paths)), inbox.put)
    if server is None:
        return
    try:
        set_dpi_aware()
        root = tk.Tk()
        try:
            shell_menu.sync_on_startup(Config.load().context_menu)
        except UserError:
            pass
        ProgressWindow(root, server, inbox, [p for p in initial_paths if os.path.exists(p)])
        root.mainloop()
    finally:
        server.close()
