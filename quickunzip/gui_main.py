# quickunzip/gui_main.py — 主窗口：拖入或选择文件 / 文件夹、历史下拉、输出路径、开始 / 停止、一行进度与结果
#
# 用法：from quickunzip import gui_main；gui_main.run()
# 配套文件：quickunzip/gui_settings.py / quickunzip/uninstall.py / quickunzip/single_instance.py / quickunzip/shell_menu.py / quickunzip/dnd.py / quickunzip/core.py / quickunzip/config.py / quickunzip/paths.py / jieya.py

import os
import queue
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from . import core, dnd, paths, shell_menu, single_instance, uninstall
from .config import Config, UserError, norm_path
from .gui_settings import SettingsWindow

APP_TITLE = 'QuickUnzip'
HINT = '把文件或文件夹拖到这里\n或点击下方"选择文件" / "选择文件夹"'
POLL_MS = 100
REMOTE_BURST_S = 1.5


def _short(text, limit=48):
    """过长的文件名截掉中间,保证进度只占一行。"""
    if len(text) <= limit:
        return text
    half = (limit - 1) // 2
    return f"{text[:half]}…{text[-half:]}"


def set_dpi_aware():
    """高分屏下避免界面被系统拉伸发糊;必须在创建 Tk 之前调用。"""
    if os.name != 'nt':
        return
    import ctypes
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except (AttributeError, OSError):
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except (AttributeError, OSError):
            pass


def resolve_output(text):
    """输出路径必须是带盘符或 UNC 的完整路径;相对路径的含义取决于启动目录,不接受,返回 None。"""
    expanded = os.path.expandvars(os.path.expanduser(text.strip()))
    if not expanded or not os.path.splitdrive(expanded)[0] or not os.path.isabs(expanded):
        return None
    return norm_path(expanded)


def open_path(path):
    try:
        os.startfile(path)
    except OSError as e:
        messagebox.showerror(APP_TITLE, f"无法打开: {path}\n{e}")


class LinkLabel(ttk.Label):
    """可点击的超链接样式文字。"""

    def __init__(self, master, text, command):
        super().__init__(master, text=text, style='Link.TLabel', cursor='hand2')
        self.bind('<Button-1>', lambda _e: command())


class HistoryPopup(tk.Toplevel):
    """输入框下方的历史下拉:最近 3 条,点击选用,✕ 删除。"""

    def __init__(self, master, anchor, entries, on_pick, on_delete):
        super().__init__(master)
        self.overrideredirect(True)
        self.on_pick = on_pick
        self.on_delete = on_delete
        frame = tk.Frame(self, bg='#c8c8c8', padx=1, pady=1)
        frame.pack(fill='both', expand=True)
        body = ttk.Frame(frame, padding=4)
        body.pack(fill='both', expand=True)

        if not entries:
            ttk.Label(body, text='暂无历史任务', style='Hint.TLabel',
                      padding=(8, 6)).pack(anchor='w')
        for h in entries:
            self._add_row(body, h)

        anchor.update_idletasks()
        x, y = anchor.winfo_rootx(), anchor.winfo_rooty() + anchor.winfo_height()
        self.geometry(f"+{x}+{y}")
        self.minsize(anchor.winfo_width(), 1)
        self.bind('<Escape>', lambda _e: self.close())
        self.bind('<Button-1>', self._click_outside)
        self.grab_set()
        self.focus_set()

    def _add_row(self, body, h):
        row = ttk.Frame(body, style='Row.TFrame', padding=(6, 3))
        row.pack(fill='x')
        texts = ttk.Frame(row, style='Row.TFrame')
        texts.pack(side='left', fill='x', expand=True)
        missing = not os.path.isdir(h['file_path'])
        main = ttk.Label(texts, text=h['file_path'] + ('  (文件夹不存在)' if missing else ''),
                         style='Row.TLabel')
        main.pack(anchor='w')
        sub = ttk.Label(texts, text=f"→ {h['extract_path']}", style='RowSub.TLabel')
        sub.pack(anchor='w')
        close = ttk.Label(row, text='✕', style='RowX.TLabel', cursor='hand2', padding=(8, 0))
        close.pack(side='right')

        pick = lambda _e: self.after_idle(self._pick, h)
        for w in (row, texts, main, sub):
            w.bind('<Button-1>', pick)
            w.bind('<Enter>', lambda _e, r=row: r.configure(style='RowHover.TFrame'))
            w.bind('<Leave>', lambda _e, r=row: r.configure(style='Row.TFrame'))
        close.bind('<Button-1>', lambda _e: self.after_idle(self._delete, h))

    def _click_outside(self, e):
        x0, y0 = self.winfo_rootx(), self.winfo_rooty()
        if not (x0 <= e.x_root < x0 + self.winfo_width()
                and y0 <= e.y_root < y0 + self.winfo_height()):
            self.close()

    def _pick(self, h):
        self.close()
        self.on_pick(h)

    def _delete(self, h):
        self.close()
        self.on_delete(h)

    def close(self):
        try:
            self.grab_release()
            self.destroy()
        except tk.TclError:
            pass


class MainWindow:
    def __init__(self, root, cfg, initial_inputs=(), inbox=None):
        self.root = root
        self.cfg = cfg
        self.inbox = inbox
        self.last_remote = time.monotonic()
        self.inputs = []
        self.output_dirty = False
        self.finished = False
        self.worker = None
        self.closing = False
        self.stop_event = threading.Event()
        self.events = queue.Queue()
        self.last_output = None

        root.title(APP_TITLE)
        if paths.ICON_FILE:
            try:
                root.iconbitmap(default=paths.ICON_FILE)
            except tk.TclError:
                pass
        root.minsize(560, 420)
        root.protocol('WM_DELETE_WINDOW', self.on_close)
        self._init_styles()
        self._build_menu()
        self._build_body()
        self.drop_enabled = dnd.enable(root, self.on_drop)
        self.add_inputs(initial_inputs)
        if inbox is not None:
            root.after(POLL_MS, self._poll_inbox)

    # ---------- 界面 ----------

    def _init_styles(self):
        style = ttk.Style(self.root)
        if 'vista' in style.theme_names():
            style.theme_use('vista')
        bg = '#ffffff'
        style.configure('Link.TLabel', foreground='#0b62c4', font=('Microsoft YaHei UI', 9, 'underline'))
        style.configure('Hint.TLabel', foreground='#888888')
        style.configure('Status.TLabel', font=('Microsoft YaHei UI', 9))
        style.configure('Counts.TLabel', font=('Microsoft YaHei UI', 10, 'bold'))
        style.configure('Row.TFrame', background=bg)
        style.configure('RowHover.TFrame', background='#e5f1fb')
        style.configure('Row.TLabel', background=bg)
        style.configure('RowSub.TLabel', background=bg, foreground='#888888')
        style.configure('RowX.TLabel', background=bg, foreground='#999999')
        style.configure('Start.TButton', font=('Microsoft YaHei UI', 10, 'bold'), padding=(16, 4))

    def _build_menu(self):
        menubar = tk.Menu(self.root)
        menu = tk.Menu(menubar, tearoff=False)
        menu.add_command(label='设置…', command=self.open_settings)
        menu.add_command(label='卸载…', command=self.uninstall)
        menu.add_separator()
        menu.add_command(label='关于', command=self.show_about)
        menu.add_command(label='退出', command=self.on_close)
        menubar.add_cascade(label='菜单', menu=menu)
        self.root.config(menu=menubar)
        self.menu = menu

    def _build_body(self):
        body = ttk.Frame(self.root, padding=12)
        body.pack(fill='both', expand=True)

        # 待解压列表(也是主要的拖放区域)
        box = ttk.LabelFrame(body, text='待解压', padding=6)
        box.pack(fill='both', expand=True)
        list_frame = ttk.Frame(box)
        list_frame.pack(fill='both', expand=True)
        self.listbox = tk.Listbox(list_frame, selectmode='extended', activestyle='none',
                                  borderwidth=1, relief='solid', highlightthickness=0)
        scroll = ttk.Scrollbar(list_frame, orient='vertical', command=self.listbox.yview)
        self.listbox.configure(yscrollcommand=scroll.set)
        self.listbox.pack(side='left', fill='both', expand=True)
        scroll.pack(side='right', fill='y')
        self.listbox.bind('<Delete>', lambda _e: self.remove_selected())
        self.listbox.bind('<Button-3>', self._show_list_menu)
        self.list_menu = tk.Menu(self.root, tearoff=False)
        self.list_menu.add_command(label='移除', command=self.remove_selected)
        self.list_menu.add_command(label='清空列表', command=self.clear_inputs)
        self.hint = tk.Label(self.listbox, text=HINT, fg='#999999', bg='#ffffff',
                             justify='center')

        row = ttk.Frame(box)
        row.pack(fill='x', pady=(6, 0))
        self.btn_files = ttk.Button(row, text='选择文件', command=self.choose_files)
        self.btn_folder = ttk.Button(row, text='选择文件夹', command=self.choose_folder)
        self.btn_history = ttk.Button(row, text='历史 ▾', command=self.show_history)
        self.btn_clear = ttk.Button(row, text='清空', command=self.clear_inputs)
        self.btn_files.pack(side='left')
        self.btn_folder.pack(side='left', padx=(6, 0))
        self.btn_history.pack(side='left', padx=(6, 0))
        self.btn_clear.pack(side='right')

        # 输出路径
        out = ttk.Frame(body)
        out.pack(fill='x', pady=(10, 0))
        ttk.Label(out, text='输出到:').pack(side='left')
        self.output_var = tk.StringVar()
        self._setting_output = False
        self.output_var.trace_add('write', self._on_output_edit)
        self.output_entry = ttk.Entry(out, textvariable=self.output_var)
        self.output_entry.pack(side='left', fill='x', expand=True, padx=(6, 6))
        self.btn_browse = ttk.Button(out, text='浏览…', command=self.choose_output)
        self.btn_browse.pack(side='left')

        # 开始 / 停止
        actions = ttk.Frame(body)
        actions.pack(fill='x', pady=(10, 0))
        self.btn_start = ttk.Button(actions, text='开始', style='Start.TButton', command=self.start)
        self.btn_stop = ttk.Button(actions, text='停止', command=self.stop, state='disabled')
        self.btn_start.pack(side='left')
        self.btn_stop.pack(side='left', padx=(6, 0))

        # 进度(一行)与结果
        self.status_var = tk.StringVar(value='就绪')
        ttk.Label(body, textvariable=self.status_var, style='Status.TLabel').pack(
            anchor='w', pady=(10, 0))
        result_row = ttk.Frame(body)
        result_row.pack(fill='x', pady=(4, 0))
        self.counts_var = tk.StringVar(value='')
        ttk.Label(result_row, textvariable=self.counts_var, style='Counts.TLabel').pack(side='left')
        self.link_log = LinkLabel(result_row, '查看失败日志',
                                  lambda: open_path(paths.FAIL_LOG_FILE))
        self.link_output = LinkLabel(result_row, '打开输出文件夹',
                                     lambda: open_path(self.last_output))

        self.input_widgets = [self.btn_files, self.btn_folder, self.btn_history,
                              self.btn_clear, self.output_entry, self.btn_browse]
        self._refresh_list()

    # ---------- 输入 ----------

    def _refresh_list(self):
        self.listbox.delete(0, 'end')
        for p in self.inputs:
            self.listbox.insert('end', p)
        if self.inputs:
            self.hint.place_forget()
        else:
            self.hint.place(relx=0.5, rely=0.5, anchor='center')

    def add_inputs(self, new_paths):
        """追加任务(去重)并按规则刷新默认输出路径。运行中不接受。"""
        if self.busy:
            self.status_var.set('任务进行中, 不能添加新的文件')
            return
        if self.finished:
            self.inputs = []
            self.finished = False
            self._clear_result()
        keys = {os.path.normcase(p) for p in self.inputs}
        for p in new_paths:
            if not os.path.exists(p):
                continue
            p = norm_path(p)
            if os.path.normcase(p) not in keys:
                keys.add(os.path.normcase(p))
                self.inputs.append(p)
        self._refresh_list()
        self._suggest_output()

    def remove_selected(self):
        if self.busy:
            return
        for i in reversed(self.listbox.curselection()):
            del self.inputs[i]
        self._refresh_list()
        if not self.inputs:
            self.output_dirty = False
        self._suggest_output()

    def _show_list_menu(self, e):
        if self.busy or not self.inputs:
            return
        i = self.listbox.nearest(e.y)
        _x, y, _w, h = self.listbox.bbox(i) or (0, 0, 0, 0)
        if not y <= e.y < y + h:
            return
        if i not in self.listbox.curselection():
            self.listbox.selection_clear(0, 'end')
            self.listbox.selection_set(i)
        n = len(self.listbox.curselection())
        self.list_menu.entryconfigure(0, label=f'移除选中的 {n} 项' if n > 1 else '移除')
        self.list_menu.tk_popup(e.x_root, e.y_root)

    def clear_inputs(self):
        self.inputs = []
        self.output_dirty = False
        self.finished = False
        self._refresh_list()
        self._clear_result()
        self._set_output('')

    def _poll_inbox(self):
        while True:
            try:
                message = self.inbox.get_nowait()
            except queue.Empty:
                break
            if message and message[0] == 'paths':
                self._remote_open(message[1])
        self.root.after(POLL_MS, self._poll_inbox)

    def _remote_open(self, remote_paths):
        """右键"解压至…": 空闲时填入并切到前台;任务进行中警告一次,不排队。"""
        now = time.monotonic()
        new_burst = now - self.last_remote > REMOTE_BURST_S
        self.last_remote = now
        self.bring_to_front()
        if not remote_paths:
            return
        if self.busy:
            if new_burst:
                messagebox.showwarning(APP_TITLE, '当前有解压任务正在进行, 请等它完成后再添加。',
                                       parent=self.root)
            return
        if new_burst:
            self.clear_inputs()
        self.add_inputs(remote_paths)

    def bring_to_front(self):
        self.root.deiconify()
        self.root.lift()
        self.root.attributes('-topmost', True)
        self.root.after(300, lambda: self.root.attributes('-topmost', False))
        self.root.focus_force()

    def on_drop(self, dropped):
        self.add_inputs(dropped)

    def choose_files(self):
        files = filedialog.askopenfilenames(parent=self.root, title='选择要解压的文件')
        if files:
            self.add_inputs(files)

    def choose_folder(self):
        folder = filedialog.askdirectory(parent=self.root, title='选择要解压的文件夹')
        if folder:
            self.add_inputs([folder])

    def show_history(self):
        HistoryPopup(self.root, self.btn_history, self.cfg.recent(3),
                     self._pick_history, self._delete_history)

    def _pick_history(self, h):
        if not os.path.isdir(h['file_path']):
            messagebox.showwarning(APP_TITLE, f"文件夹不存在:\n{h['file_path']}")
            return
        self.clear_inputs()
        self.add_inputs([h['file_path']])

    def _delete_history(self, h):
        self.cfg = Config.load()
        self.cfg.remove_history(h['file_path'])
        self.cfg.save()
        self.show_history()

    # ---------- 输出路径 ----------

    def _set_output(self, value):
        self._setting_output = True
        self.output_var.set(value)
        self._setting_output = False

    def _on_output_edit(self, *_args):
        if not self._setting_output:
            self.output_dirty = True

    def _suggest_output(self):
        if self.inputs and not self.output_dirty:
            self._set_output(self.cfg.suggest_output(self.inputs))

    def choose_output(self):
        initial = self.output_var.get().strip()
        while initial and not os.path.isdir(initial):
            parent = os.path.dirname(initial)
            initial = '' if parent == initial else parent
        folder = filedialog.askdirectory(parent=self.root, title='选择输出文件夹',
                                         initialdir=initial or None)
        if folder:
            self.output_var.set(os.path.normpath(folder))

    # ---------- 执行 ----------

    @property
    def busy(self):
        return self.worker is not None and self.worker.is_alive()

    def _set_busy(self, busy):
        for w in self.input_widgets:
            w.state(['disabled'] if busy else ['!disabled'])
        self.listbox.configure(state='disabled' if busy else 'normal')
        self.btn_start.state(['disabled'] if busy else ['!disabled'])
        self.btn_stop.state(['!disabled'] if busy else ['disabled'])
        for index in (0, 1):
            self.menu.entryconfigure(index, state='disabled' if busy else 'normal')

    def _clear_result(self):
        self.status_var.set('就绪')
        self.counts_var.set('')
        self.link_log.pack_forget()
        self.link_output.pack_forget()

    def start(self):
        if self.busy:
            return
        self.inputs = [p for p in self.inputs if os.path.exists(p)]
        self._refresh_list()
        if not self.inputs:
            messagebox.showinfo(APP_TITLE, '请先拖入或选择要解压的文件 / 文件夹。')
            return
        output = self.output_var.get().strip()
        if not output:
            messagebox.showinfo(APP_TITLE, '请填写输出路径。')
            return
        resolved = resolve_output(output)
        if resolved is None:
            messagebox.showinfo(APP_TITLE, f"请填写完整的输出路径, 例如 D:\\已解压\n当前填的是: {output}")
            return
        output = resolved
        if os.path.isfile(output):
            messagebox.showerror(APP_TITLE, f"输出路径是一个文件, 不是文件夹:\n{output}")
            return
        try:
            self.cfg = Config.load()
        except UserError as e:
            messagebox.showerror(APP_TITLE, str(e))
            return

        self._set_output(output)
        self.last_output = output
        self._clear_result()
        self.status_var.set('正在准备…')
        self.stop_event.clear()
        self.worker = threading.Thread(target=self._work, args=(list(self.inputs), output, self.cfg),
                                       daemon=True)
        self._set_busy(True)
        self.worker.start()
        self.root.after(POLL_MS, self._poll)

    def stop(self):
        if self.busy:
            self.stop_event.set()
            self.btn_stop.state(['disabled'])
            self.status_var.set('正在停止… 当前压缩包处理完后停止')

    def _work(self, inputs, output, cfg):
        """后台线程: 规划 → 解压 → 记录历史;结果通过 events 队列交回界面线程。"""
        try:
            jobs = core.plan_unified(inputs, output)
            if not jobs:
                self.events.put(('empty',))
                return
            os.makedirs(output, exist_ok=True)

            def progress(done, total, current, result):
                self.events.put(('progress', done, total, current,
                                 result.succeeded, result.failed))

            extractor = core.Extractor.from_config(cfg, on_progress=progress,
                                                   stop_event=self.stop_event)
            result = extractor.run(jobs)
            history_error = None
            try:
                cfg.record_run(inputs, output)
            except OSError as e:
                history_error = str(e)
            self.events.put(('done', result, history_error))
        except UserError as e:
            self.events.put(('error', str(e)))
        except Exception as e:
            self.events.put(('error', f"{type(e).__name__}: {e}"))

    def _poll(self):
        while True:
            try:
                event = self.events.get_nowait()
            except queue.Empty:
                break
            self._handle(event)
        if self.busy or not self.events.empty():
            self.root.after(POLL_MS, self._poll)
        else:
            self._set_busy(False)
            if self.closing:
                self.root.destroy()

    def _handle(self, event):
        kind = event[0]
        if kind == 'progress':
            _, done, total, current, ok, failed = event
            if current is not None and not self.stop_event.is_set():
                self.status_var.set(f"[{done + 1}/{total}] 正在解压: {_short(current)}")
            self.counts_var.set(f"成功 {ok} 个 / 失败 {failed} 个")
        elif kind == 'done':
            self._show_result(event[1])
            if event[2]:
                messagebox.showwarning(APP_TITLE, f"解压已完成, 但保存历史记录失败:\n{event[2]}",
                                       parent=self.root)
        elif kind == 'empty':
            self.status_var.set('没有可解压的文件')
            self.finished = True
        elif kind == 'error':
            self.status_var.set('出错了')
            self.finished = True
            messagebox.showerror(APP_TITLE, event[1])

    def _show_result(self, result):
        self.finished = True
        self.output_dirty = False
        if result.stopped:
            line = f"已停止, 未处理 {result.skipped} 个"
        else:
            line = f"完成, 共 {result.total} 个"
        extras = []
        if result.purged:
            extras.append(f"清理垃圾 {result.purged}")
        if result.renamed:
            extras.append(f"重命名 {result.renamed}")
        if result.deleted:
            extras.append(f"源文件移到回收站 {result.deleted}")
        if result.kept_no_recycle_bin:
            extras.append(f"{result.kept_no_recycle_bin} 个源文件所在位置没有回收站, 未删除")
        if extras:
            line += ' (' + ', '.join(extras) + ')'
        self.status_var.set(line)
        self.counts_var.set(f"成功 {result.succeeded} 个 / 失败 {result.failed} 个")
        if result.failures and result.log_file:
            self.link_log.pack(side='left', padx=(12, 0))
        elif result.failures:
            self.status_var.set(f"{line}  (失败日志写入失败: {paths.FAIL_LOG_FILE})")
        if result.produced:
            self.link_output.pack(side='left', padx=(12, 0))

    # ---------- 其他 ----------

    def open_settings(self):
        if self.busy:
            return
        try:
            SettingsWindow(self.root, on_saved=self._settings_saved)
        except UserError as e:
            messagebox.showerror(APP_TITLE, str(e))

    def _settings_saved(self):
        self.cfg = Config.load()
        self._suggest_output()

    def uninstall(self):
        if self.busy:
            return
        frozen = uninstall.is_frozen()
        if frozen:
            detail = ('将删除: 右键菜单、QuickUnzip.exe、_internal、config.json\n'
                      '保留: 待解压/、已解压/ 及其中的文件, 解压失败日志.txt')
        else:
            detail = '源码运行: 只清除右键菜单并在配置里关闭它, 不删除任何文件。'
        if not messagebox.askyesno(APP_TITLE, f"确定卸载 QuickUnzip 吗?\n\n{detail}",
                                   icon='warning', parent=self.root):
            return
        try:
            cfg = Config.load()
        except UserError as e:
            messagebox.showerror(APP_TITLE, str(e), parent=self.root)
            return

        exported = None
        if cfg.passwords:
            exported = filedialog.asksaveasfilename(
                parent=self.root, title='导出密码本(一行一个密码)',
                initialdir=uninstall.default_export_dir(),
                initialfile=uninstall.PASSWORD_FILE_NAME,
                defaultextension='.txt', filetypes=[('文本文件', '*.txt')])
            if exported:
                try:
                    uninstall.export_passwords(exported, cfg.passwords)
                except OSError as e:
                    messagebox.showerror(APP_TITLE, f"导出密码本失败, 已取消卸载:\n{e}",
                                         parent=self.root)
                    return
            elif not messagebox.askyesno(APP_TITLE, '没有导出密码本, 卸载后密码库将无法找回。\n'
                                                    '仍要继续卸载吗?', icon='warning',
                                         parent=self.root):
                return

        try:
            uninstall.remove_registry()
        except OSError as e:
            messagebox.showerror(APP_TITLE, f"清除右键菜单失败, 已取消卸载:\n{e}", parent=self.root)
            return
        done = f"\n密码本已导出到:\n{exported}" if exported else ''
        if not frozen:
            cfg.data['context_menu'] = {k: False for k in cfg.context_menu}
            cfg.save()
            messagebox.showinfo(APP_TITLE, f"已清除右键菜单。{done}", parent=self.root)
            return
        uninstall.schedule_self_delete()
        messagebox.showinfo(APP_TITLE, f"卸载完成, 关闭此提示后程序文件将被删除。{done}",
                            parent=self.root)
        self.root.destroy()

    def show_about(self):
        messagebox.showinfo(APP_TITLE, 'QuickUnzip\n批量递归解压 · 密码库 · 垃圾清理 · 重命名\n\n'
                                       'https://github.com/Momordicin/QuickUnzip')

    def on_close(self):
        if not self.busy:
            self.root.destroy()
            return
        if messagebox.askyesno(APP_TITLE, '任务进行中。\n停止并退出吗? (当前压缩包处理完后退出)'):
            self.closing = True
            self.stop()


def warn_shared_folder(root, cfg):
    """程序文件夹里混有其他文件时提醒一次:QuickUnzip 应单独放一个文件夹,卸载时只删程序自己的文件。"""
    if cfg.data['warned_shared_folder']:
        return
    others = uninstall.foreign_entries()
    if not others:
        return
    cfg.data['warned_shared_folder'] = True
    try:
        cfg.save()
    except OSError:
        pass
    shown = '\n'.join(f"  {n}" for n in others[:8]) + ('\n  …' if len(others) > 8 else '')
    messagebox.showwarning(
        APP_TITLE,
        f"QuickUnzip 所在的文件夹里还有其他文件:\n{shown}\n\n"
        f"请把 QuickUnzip 单独放在一个文件夹里(直接解压发布包会自动生成 QuickUnzip 文件夹)。\n"
        f"卸载时只会删除程序自己的文件, 上面这些不会被删除。",
        parent=root)


def run(initial_inputs=()):
    """创建并运行主窗口;已有主窗口在运行时把 initial_inputs 交给它后直接返回。"""
    inbox = queue.Queue()
    server = single_instance.deliver_or_serve('main', ('paths', list(initial_inputs)), inbox.put)
    if server is None:
        return
    try:
        set_dpi_aware()
        root = tk.Tk()
        try:
            cfg = Config.load()
        except UserError as e:
            root.withdraw()
            messagebox.showerror(APP_TITLE, str(e))
            root.destroy()
            return
        shell_menu.sync_on_startup(cfg.context_menu)
        core.consolidate_log()
        MainWindow(root, cfg, initial_inputs, inbox)
        if uninstall.is_frozen():
            root.after(300, warn_shared_folder, root, cfg)
        root.mainloop()
    finally:
        server.close()
