# quickunzip/shell_menu.py — 在 HKCU 注册 / 删除资源管理器右键菜单（文件 / 文件夹 / 文件夹空白处 × 智能解压到此处 / 解压至…）
#
# 用法：from quickunzip import shell_menu；shell_menu.apply(cfg.context_menu)；shell_menu.remove_all()
# 配套文件：quickunzip/paths.py / quickunzip/gui_settings.py / quickunzip/gui_main.py / jieya.py / tests/test_shell_menu.py

import os
import sys

from . import paths

CLASSES_ROOT = r'Software\Classes'
ARCHIVE_EXTS = ('.zip', '.7z', '.rar', '.tar', '.gz', '.tgz', '.bz2', '.xz', '.001')
VERBS = (
    ('QuickUnzip.Here', '智能解压到此处', '--here'),
    ('QuickUnzip.To', '解压至…', '--to-dialog'),
)
LOCATIONS = {
    'file': ([rf'SystemFileAssociations\{ext}\shell' for ext in ARCHIVE_EXTS], '%1'),
    'folder': ([r'Directory\shell'], '%1'),
    'background': ([r'Directory\Background\shell'], '%V'),
}


def launcher():
    """右键命令里启动本程序的部分:打包后是 exe,源码运行时是 pythonw + jieya.py。"""
    if getattr(sys, 'frozen', False):
        return [sys.executable]
    exe_dir = os.path.dirname(sys.executable)
    pythonw = os.path.join(exe_dir, 'pythonw.exe')
    return [pythonw if os.path.isfile(pythonw) else sys.executable,
            os.path.join(paths.APP_DIR, 'jieya.py')]


def _icon():
    if getattr(sys, 'frozen', False):
        return f'{sys.executable},0'
    return paths.ICON_FILE or ''


def desired_entries(context_menu):
    """{注册表键路径(相对 Classes): {值名: 值}};command 子键用 'command' 表示。"""
    prefix = ' '.join(f'"{p}"' for p in launcher())
    entries = {}
    for location, (shell_keys, placeholder) in LOCATIONS.items():
        if not context_menu.get(location):
            continue
        for shell_key in shell_keys:
            for verb, text, flag in VERBS:
                entries[rf'{shell_key}\{verb}'] = {
                    'MUIVerb': text,
                    'Icon': _icon(),
                    'MultiSelectModel': 'Player',
                    'command': f'{prefix} {flag} "{placeholder}"',
                }
    return entries


def all_entry_keys():
    return [rf'{shell_key}\{verb}'
            for shell_keys, _ in LOCATIONS.values()
            for shell_key in shell_keys
            for verb, _, _ in VERBS]


def _read_entry(winreg, root, key_path):
    full = rf'{root}\{key_path}'
    values = {}
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, full) as key:
            for name in ('MUIVerb', 'Icon', 'MultiSelectModel'):
                try:
                    values[name] = winreg.QueryValueEx(key, name)[0]
                except FileNotFoundError:
                    pass
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, rf'{full}\command') as key:
            values['command'] = winreg.QueryValueEx(key, '')[0]
    except FileNotFoundError:
        return None
    return values


def _write_entry(winreg, root, key_path, values):
    full = rf'{root}\{key_path}'
    with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, full, 0, winreg.KEY_WRITE) as key:
        for name in ('MUIVerb', 'Icon', 'MultiSelectModel'):
            winreg.SetValueEx(key, name, 0, winreg.REG_SZ, values[name])
    with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, rf'{full}\command', 0,
                            winreg.KEY_WRITE) as key:
        winreg.SetValueEx(key, '', 0, winreg.REG_SZ, values['command'])


def _delete_entry(winreg, root, key_path):
    full = rf'{root}\{key_path}'
    existed = False
    for sub in (rf'{full}\command', full):
        try:
            winreg.DeleteKey(winreg.HKEY_CURRENT_USER, sub)
            existed = True
        except FileNotFoundError:
            pass
    return existed


def _notify_shell():
    import ctypes
    SHCNE_ASSOCCHANGED = 0x08000000
    SHCNF_IDLIST = 0x0000
    ctypes.windll.shell32.SHChangeNotify(SHCNE_ASSOCCHANGED, SHCNF_IDLIST, None, None)


def apply(context_menu, root=CLASSES_ROOT):
    """让注册表与 context_menu 开关一致(路径变了也会改写)。返回是否有改动。"""
    if os.name != 'nt':
        return False
    import winreg
    wanted = desired_entries(context_menu)
    changed = False
    for key_path in all_entry_keys():
        if key_path in wanted:
            if _read_entry(winreg, root, key_path) != wanted[key_path]:
                _write_entry(winreg, root, key_path, wanted[key_path])
                changed = True
        elif _delete_entry(winreg, root, key_path):
            changed = True
    if changed and root == CLASSES_ROOT:
        _notify_shell()
    return changed


def remove_all(root=CLASSES_ROOT):
    return apply({}, root)


def sync_on_startup(context_menu):
    """每次启动时调用:按配置注册,程序文件夹被挪走后自动改写右键命令路径。"""
    try:
        return apply(context_menu)
    except OSError:
        return False
