# quickunzip/uninstall.py — 卸载：导出密码本、清除右键菜单、程序退出后由临时 PowerShell 脚本删除程序文件（保留 待解压/ 已解压/ 与失败日志）
#
# 用法：from quickunzip import uninstall；uninstall.export_passwords(path, passwords)；uninstall.remove_registry()；uninstall.schedule_self_delete()
# 配套文件：quickunzip/gui_main.py / quickunzip/shell_menu.py / quickunzip/paths.py / tests/test_uninstall.py

import os
import re
import subprocess
import sys
import tempfile

from . import core, paths, shell_menu

PASSWORD_FILE_NAME = 'QuickUnzip密码本.txt'
PROGRAM_FILES = ('QuickUnzip.exe', '_internal', 'config.json', 'README.md', 'LICENSE')
KEPT_NAMES = ('待解压', '已解压', paths.FAIL_LOG_NAME, core.WORK_DIR_NAME)
TEMP_RE = re.compile(r'^(config|faillog)\..+\.tmp$')
SIGNATURES = {
    'config.json': ('"passwords"', '"post_process"'),
    'README.md': ('QuickUnzip',),
    'LICENSE': ('MIT License', 'Momordicin'),
}


def is_frozen():
    return bool(getattr(sys, 'frozen', False))


def default_export_dir():
    desktop = os.path.join(os.path.expanduser('~'), 'Desktop')
    return desktop if os.path.isdir(desktop) else os.path.expanduser('~')


def export_passwords(path, passwords):
    with open(path, 'w', encoding='utf-8') as f:
        for pw in passwords:
            f.write(f"{pw}\n")


def remove_registry():
    return shell_menu.remove_all()


def _is_ours(path):
    """同名文件要看内容: config.json / README.md / LICENSE 里有本程序的特征才算。"""
    name = os.path.basename(path)
    if name not in SIGNATURES:
        return os.path.exists(path)
    try:
        with open(path, encoding='utf-8', errors='replace') as f:
            text = f.read(200000)
    except OSError:
        return False
    return all(mark in text for mark in SIGNATURES[name])


def _listdir(app_dir):
    try:
        return os.listdir(app_dir)
    except OSError:
        return []


def delete_targets(app_dir=None):
    """卸载要删除的路径:程序文件(同名文件核对内容)和本程序留下的临时文件。"""
    app_dir = app_dir or paths.APP_DIR
    targets = [os.path.join(app_dir, name) for name in PROGRAM_FILES
               if _is_ours(os.path.join(app_dir, name))]
    return targets + [os.path.join(app_dir, n) for n in _listdir(app_dir) if TEMP_RE.match(n)]


def foreign_entries(app_dir=None):
    """程序文件夹里不属于本程序的文件 / 文件夹名;程序应当放在单独的文件夹里。"""
    app_dir = app_dir or paths.APP_DIR
    own = set(PROGRAM_FILES) | set(KEPT_NAMES)
    return sorted(n for n in _listdir(app_dir)
                  if not TEMP_RE.match(n)
                  and not (n in own and _is_ours(os.path.join(app_dir, n))))


def _ps_quote(text):
    return "'" + text.replace("'", "''") + "'"


def build_script(pid, app_dir, targets):
    """等进程 pid 退出后删除 targets;程序文件夹删空了就连文件夹一起删;最后删除脚本自身。"""
    lines = [
        f"Wait-Process -Id {pid} -Timeout 60 -ErrorAction SilentlyContinue",
        "Start-Sleep -Milliseconds 500",
        f"$targets = @({', '.join(_ps_quote(t) for t in targets)})",
        "foreach ($t in $targets) {",
        "    for ($i = 0; $i -lt 20 -and (Test-Path -LiteralPath $t); $i++) {",
        "        Remove-Item -LiteralPath $t -Recurse -Force -ErrorAction SilentlyContinue",
        "        if (Test-Path -LiteralPath $t) { Start-Sleep -Milliseconds 500 }",
        "    }",
        "}",
        f"$dir = {_ps_quote(app_dir)}",
        "if ((Test-Path -LiteralPath $dir) -and -not (Get-ChildItem -LiteralPath $dir -Force)) {",
        "    Set-Location $env:TEMP",
        "    Remove-Item -LiteralPath $dir -Force -ErrorAction SilentlyContinue",
        "}",
        "Remove-Item -LiteralPath $PSCommandPath -Force -ErrorAction SilentlyContinue",
    ]
    return '\r\n'.join(lines) + '\r\n'


def schedule_self_delete(app_dir=None):
    """写临时脚本并在后台启动;调用方随后应立即退出程序。返回脚本路径。"""
    app_dir = app_dir or paths.APP_DIR
    script = build_script(os.getpid(), app_dir, delete_targets(app_dir))
    fd, path = tempfile.mkstemp(prefix='QuickUnzip-uninstall-', suffix='.ps1')
    with os.fdopen(fd, 'w', encoding='utf-8-sig', newline='') as f:
        f.write(script)
    subprocess.Popen(
        ['powershell.exe', '-NoProfile', '-ExecutionPolicy', 'Bypass',
         '-WindowStyle', 'Hidden', '-File', path],
        cwd=tempfile.gettempdir(),
        creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0)
        | getattr(subprocess, 'CREATE_NEW_PROCESS_GROUP', 0),
        close_fds=True)
    return path
