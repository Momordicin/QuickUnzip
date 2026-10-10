# quickunzip/gui_settings.py — 设置窗口：密码库、常用路径、右键菜单、解压后处理、垃圾清单在同一页上下滚动，点标签跳到对应位置
#
# 用法：from quickunzip.gui_settings import SettingsWindow；SettingsWindow(root, on_saved=callback)
# 配套文件：quickunzip/gui_main.py / quickunzip/config.py / quickunzip/shell_menu.py

import copy
import os
import time
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from . import shell_menu
from .config import Config, UserError, norm_path

APP_TITLE = 'QuickUnzip 设置'
SECTIONS = ['密码库', '常用路径', '右键菜单', '解压后处理', '垃圾清单']


class ListEditor(ttk.Frame):
    """可增删改、可调整顺序的字符串列表。"""

    def __init__(self, master, items, unit):
        super().__init__(master)
        self.unit = unit
        self.items = list(items)

        top = ttk.Frame(self)
        top.pack(fill='both', expand=True)
        self.listbox = tk.Listbox(top, height=8, activestyle='none', exportselection=False,
                                  borderwidth=1, relief='solid', highlightthickness=0)
        scroll = ttk.Scrollbar(top, orient='vertical', command=self.listbox.yview)
        self.listbox.configure(yscrollcommand=scroll.set)
        self.listbox.pack(side='left', fill='both', expand=True)
        scroll.pack(side='left', fill='y')
        side = ttk.Frame(top, padding=(8, 0, 0, 0))
        side.pack(side='left', fill='y')
        ttk.Button(side, text='上移', command=lambda: self.move(-1)).pack(fill='x')
        ttk.Button(side, text='下移', command=lambda: self.move(1)).pack(fill='x', pady=(4, 0))
        ttk.Button(side, text='删除', command=self.delete).pack(fill='x', pady=(12, 0))

        bottom = ttk.Frame(self)
        bottom.pack(fill='x', pady=(6, 0))
        self.entry_var = tk.StringVar()
        entry = ttk.Entry(bottom, textvariable=self.entry_var)
        entry.pack(side='left', fill='x', expand=True)
        entry.bind('<Return>', lambda _e: self.add())
        ttk.Button(bottom, text='添加', command=self.add).pack(side='left', padx=(6, 0))
        ttk.Button(bottom, text='修改选中', command=self.replace).pack(side='left', padx=(6, 0))
        self.count_var = tk.StringVar()
        ttk.Label(self, textvariable=self.count_var, style='Hint.TLabel').pack(anchor='w', pady=(4, 0))

        self.listbox.bind('<<ListboxSelect>>', self._on_select)
        self.listbox.bind('<Delete>', lambda _e: self.delete())
        self._refresh()

    def _refresh(self, select=None):
        self.listbox.delete(0, 'end')
        for item in self.items:
            self.listbox.insert('end', item)
        if select is not None and 0 <= select < len(self.items):
            self.listbox.selection_set(select)
            self.listbox.see(select)
        self.count_var.set(f"共 {len(self.items)} {self.unit}")

    def _selected(self):
        sel = self.listbox.curselection()
        return sel[0] if sel else None

    def _on_select(self, _e):
        i = self._selected()
        if i is not None:
            self.entry_var.set(self.items[i])

    def _value(self):
        value = self.entry_var.get().strip()
        if not value:
            return None
        return value

    def add(self):
        value = self._value()
        if value is None:
            return
        if value in self.items:
            self._refresh(self.items.index(value))
            return
        self.items.append(value)
        self.entry_var.set('')
        self._refresh(len(self.items) - 1)

    def replace(self):
        i, value = self._selected(), self._value()
        if i is None or value is None:
            return
        if value in self.items and self.items.index(value) != i:
            messagebox.showinfo(APP_TITLE, f"列表里已经有 {value}", parent=self)
            return
        self.items[i] = value
        self._refresh(i)

    def delete(self):
        i = self._selected()
        if i is None:
            return
        del self.items[i]
        self.entry_var.set('')
        self._refresh(min(i, len(self.items) - 1))

    def move(self, step):
        i = self._selected()
        if i is None or not 0 <= i + step < len(self.items):
            return
        self.items[i], self.items[i + step] = self.items[i + step], self.items[i]
        self._refresh(i + step)


class HistoryEditor(ttk.Frame):
    """常用路径: 输入文件夹 → 输出文件夹 映射表。"""

    def __init__(self, master, history):
        super().__init__(master)
        self.history = copy.deepcopy(history)

        top = ttk.Frame(self)
        top.pack(fill='both', expand=True)
        self.tree = ttk.Treeview(top, columns=('src', 'dst', 'used'), show='headings', height=7,
                                 selectmode='browse')
        for col, text, width in (('src', '输入文件夹', 210), ('dst', '输出文件夹', 210),
                                 ('used', '最近使用', 130)):
            self.tree.heading(col, text=text, anchor='w')
            self.tree.column(col, width=width, anchor='w', stretch=col != 'used')
        scroll = ttk.Scrollbar(top, orient='vertical', command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.pack(side='left', fill='both', expand=True)
        scroll.pack(side='left', fill='y')

        buttons = ttk.Frame(self)
        buttons.pack(fill='x', pady=(6, 0))
        ttk.Button(buttons, text='添加…', command=self.add).pack(side='left')
        ttk.Button(buttons, text='修改输出文件夹…', command=self.change_output).pack(
            side='left', padx=(6, 0))
        ttk.Button(buttons, text='删除', command=self.delete).pack(side='left', padx=(6, 0))
        self.count_var = tk.StringVar()
        ttk.Label(buttons, textvariable=self.count_var, style='Hint.TLabel').pack(side='right')
        self.tree.bind('<Delete>', lambda _e: self.delete())
        self.tree.bind('<Double-1>', lambda _e: self.change_output())
        self._refresh()

    def _refresh(self, select=None):
        self.tree.delete(*self.tree.get_children())
        for i, h in enumerate(self.history):
            self.tree.insert('', 'end', iid=str(i),
                             values=(h['file_path'], h['extract_path'], h.get('last_used', '')))
        if select is not None and 0 <= select < len(self.history):
            self.tree.selection_set(str(select))
            self.tree.see(str(select))
        self.count_var.set(f"共 {len(self.history)} 条, 主窗口下拉显示最近 3 条")

    def _selected(self):
        sel = self.tree.selection()
        return int(sel[0]) if sel else None

    def _ask_folder(self, title, initial=None):
        folder = filedialog.askdirectory(parent=self, title=title,
                                         initialdir=initial if initial and os.path.isdir(initial) else None)
        return norm_path(folder) if folder else None

    def add(self):
        src = self._ask_folder('选择输入文件夹')
        if not src:
            return
        dst = self._ask_folder('选择输出文件夹', src)
        if not dst:
            return
        key = os.path.normcase(src)
        self.history = [h for h in self.history if os.path.normcase(norm_path(h['file_path'])) != key]
        self.history.insert(0, {"file_path": src, "extract_path": dst,
                                "last_used": time.strftime('%Y-%m-%d %H:%M:%S')})
        self._refresh(0)

    def change_output(self):
        i = self._selected()
        if i is None:
            return
        dst = self._ask_folder('选择输出文件夹', self.history[i]['extract_path'])
        if dst:
            self.history[i]['extract_path'] = dst
            self._refresh(i)

    def delete(self):
        i = self._selected()
        if i is None:
            return
        del self.history[i]
        self._refresh(min(i, len(self.history) - 1))


class SettingsWindow(tk.Toplevel):
    def __init__(self, master, on_saved=None):
        cfg = Config.load()
        super().__init__(master)
        self.on_saved = on_saved
        self.cfg = cfg
        self.original = copy.deepcopy(self.cfg.data)

        self.title(APP_TITLE)
        self.geometry('680x600')
        self.minsize(560, 420)
        self.transient(master)
        self.protocol('WM_DELETE_WINDOW', self.cancel)
        self._init_styles()

        self.tab_var = tk.StringVar(value=SECTIONS[0])
        tabs = ttk.Frame(self, padding=(12, 10, 12, 0))
        tabs.pack(fill='x')
        for name in SECTIONS:
            ttk.Radiobutton(tabs, text=name, value=name, variable=self.tab_var,
                            style='Tab.Toolbutton', command=lambda n=name: self.jump(n)).pack(
                side='left', padx=(0, 4))
        ttk.Separator(self).pack(fill='x', pady=(8, 0))

        footer = ttk.Frame(self, padding=12)
        footer.pack(side='bottom', fill='x')
        ttk.Button(footer, text='取消', command=self.cancel).pack(side='right')
        ttk.Button(footer, text='保存', command=self.save).pack(side='right', padx=(0, 6))
        ttk.Separator(self).pack(side='bottom', fill='x')

        holder = ttk.Frame(self)
        holder.pack(fill='both', expand=True)
        self.canvas = tk.Canvas(holder, highlightthickness=0, borderwidth=0)
        vscroll = ttk.Scrollbar(holder, orient='vertical', command=self._scrollbar_moved)
        self.canvas.configure(yscrollcommand=lambda a, b: (vscroll.set(a, b), self._sync_tab()))
        vscroll.pack(side='right', fill='y')
        self.canvas.pack(side='left', fill='both', expand=True)
        self.page = ttk.Frame(self.canvas, padding=(16, 4, 16, 16))
        self.page_id = self.canvas.create_window(0, 0, window=self.page, anchor='nw')
        self.page.bind('<Configure>', lambda _e: self.canvas.configure(
            scrollregion=self.canvas.bbox('all')))
        self.canvas.bind('<Configure>', lambda e: self.canvas.itemconfigure(
            self.page_id, width=e.width))
        self.bind_all('<MouseWheel>', self._on_wheel)
        self.bind('<Destroy>', self._on_destroy)

        self.sections = {}
        self.pinned_tab = None
        self._build_passwords()
        self._build_history()
        self._build_context_menu()
        self._build_post_process()
        self._build_garbage()

        self.grab_set()
        self.focus_set()

    def _init_styles(self):
        style = ttk.Style(self)
        style.configure('Tab.Toolbutton', padding=(12, 4))
        style.configure('Section.TLabel', font=('Microsoft YaHei UI', 11, 'bold'))
        style.configure('Hint.TLabel', foreground='#888888')

    # ---------- 布局 ----------

    def _section(self, name, hint=None):
        frame = ttk.Frame(self.page, padding=(0, 14, 0, 4))
        frame.pack(fill='x')
        ttk.Label(frame, text=name, style='Section.TLabel').pack(anchor='w')
        if hint:
            ttk.Label(frame, text=hint, style='Hint.TLabel', wraplength=600,
                      justify='left').pack(anchor='w', pady=(2, 0))
        body = ttk.Frame(frame, padding=(0, 8, 0, 0))
        body.pack(fill='x')
        self.sections[name] = frame
        return body

    def _build_passwords(self):
        body = self._section('密码库', '解压时按从上到下的顺序逐个尝试, 最后再试一次无密码。')
        self.passwords = ListEditor(body, self.cfg.data['passwords'], '个密码')
        self.passwords.pack(fill='x')

    def _build_history(self):
        body = self._section('常用路径', '执行过的文件夹任务: 再次选中这个输入文件夹时, 自动填上对应的输出文件夹。')
        self.history = HistoryEditor(body, self.cfg.history)
        self.history.pack(fill='x')

    def _build_context_menu(self):
        body = self._section('右键菜单', '在资源管理器右键菜单中显示"智能解压到此处"和"解压至…"。')
        cm = self.cfg.context_menu
        self.cm_vars = {k: tk.BooleanVar(value=bool(cm[k])) for k in ('file', 'folder', 'background')}
        self.cm_all_off = tk.BooleanVar(value=not any(v.get() for v in self.cm_vars.values()))
        ttk.Checkbutton(body, text='一键关闭全部右键菜单', variable=self.cm_all_off,
                        command=self._toggle_all_off).pack(anchor='w')
        ttk.Separator(body).pack(fill='x', pady=8)
        for key, text in (('file', '文件(.zip .7z .rar .tar .gz .tgz .bz2 .xz .001)'),
                          ('folder', '文件夹'),
                          ('background', '文件夹空白处')):
            ttk.Checkbutton(body, text=text, variable=self.cm_vars[key],
                            command=self._sync_all_off).pack(anchor='w', pady=2)

    def _toggle_all_off(self):
        on = not self.cm_all_off.get()
        for v in self.cm_vars.values():
            v.set(on)

    def _sync_all_off(self):
        self.cm_all_off.set(not any(v.get() for v in self.cm_vars.values()))

    def _build_post_process(self):
        body = self._section('解压后处理', '只作用于本次解压出来的文件夹, 输出目录里原有的文件不受影响。')
        pp = self.cfg.post_process
        self.pp_vars = {k: tk.BooleanVar(value=bool(pp[k]))
                        for k in ('purge_garbage', 'rename', 'delete_source')}
        ttk.Checkbutton(body, text='垃圾清理(删除"垃圾清单"里列出的文件)',
                        variable=self.pp_vars['purge_garbage']).pack(anchor='w', pady=2)
        ttk.Checkbutton(body, text='重命名(文件夹名含 No.xxx 时, 把里面的文件改名为 "xxx - 编号")',
                        variable=self.pp_vars['rename']).pack(anchor='w', pady=2)
        ttk.Separator(body).pack(fill='x', pady=8)
        ttk.Checkbutton(body, text='删除源文件', variable=self.pp_vars['delete_source']).pack(anchor='w')
        ttk.Label(body, text='压缩包及其内部各层全部解压成功后, 把源文件(分卷则为全部分卷)移到回收站; '
                             '任何一层失败都保留源文件。',
                  style='Hint.TLabel', wraplength=600, justify='left').pack(anchor='w', padx=(22, 0))

    def _build_garbage(self):
        body = self._section('垃圾清单', '解压后删除这些文件, 需要写完整的文件名(含后缀), 例如 广告.txt。')
        self.garbage = ListEditor(body, self.cfg.data['garbage_list'], '个文件名')
        self.garbage.pack(fill='x')

    # ---------- 滚动与标签 ----------

    def jump(self, name):
        self.update_idletasks()
        total = self.page.winfo_height()
        if total > 0:
            self.canvas.yview_moveto(self.sections[name].winfo_y() / total)
        self.pinned_tab = name
        self.tab_var.set(name)

    def _scrollbar_moved(self, *args):
        self.pinned_tab = None
        self.canvas.yview(*args)

    def _sync_tab(self):
        if self.pinned_tab:
            self.tab_var.set(self.pinned_tab)
            return
        total = self.page.winfo_height()
        if total <= 0:
            return
        top, bottom = self.canvas.yview()
        if top > 0 and bottom >= 0.999:
            self.tab_var.set(SECTIONS[-1])
            return
        y = top * total + 8
        current = SECTIONS[0]
        for name in SECTIONS:
            if self.sections[name].winfo_y() <= y:
                current = name
        self.tab_var.set(current)

    def _on_wheel(self, e):
        if e.widget.winfo_toplevel() is not self:
            return
        if e.widget.winfo_class() in ('Listbox', 'Treeview'):
            return
        self.pinned_tab = None
        self.canvas.yview_scroll(int(-e.delta / 120), 'units')

    def _on_destroy(self, e):
        if e.widget is self:
            self.unbind_all('<MouseWheel>')

    # ---------- 保存 / 取消 ----------

    def _collect(self):
        data = copy.deepcopy(self.original)
        data['passwords'] = list(self.passwords.items)
        data['garbage_list'] = list(self.garbage.items)
        data['history'] = copy.deepcopy(self.history.history)
        data['context_menu'] = {k: v.get() for k, v in self.cm_vars.items()}
        data['post_process'] = {k: v.get() for k, v in self.pp_vars.items()}
        return data

    def save(self):
        try:
            self.cfg.data = self._collect()
            self.cfg.save()
        except (OSError, UserError) as e:
            messagebox.showerror(APP_TITLE, f"保存失败: {e}", parent=self)
            return
        try:
            shell_menu.apply(self.cfg.context_menu)
        except OSError as e:
            messagebox.showerror(APP_TITLE, f"设置已保存, 但写入右键菜单失败: {e}", parent=self)
        self.destroy()
        if self.on_saved:
            self.on_saved()

    def cancel(self):
        if self._collect() != self.original and not messagebox.askyesno(
                APP_TITLE, '有未保存的修改, 确定放弃吗?', parent=self):
            return
        self.destroy()
