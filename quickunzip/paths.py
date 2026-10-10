# quickunzip/paths.py — 唯一的程序路径来源：由 exe 或仓库位置得出 APP_DIR / BUNDLE_DIR，派生配置、失败日志、外部工具目录与窗口图标路径
#
# 用法：from quickunzip import paths；paths.APP_DIR / BUNDLE_DIR / TOOL_DIRS / CONFIG_FILE / CONFIG_EXAMPLE_FILES / FAIL_LOG_NAME / FAIL_LOG_FILE / ICON_FILE
# 配套文件：quickunzip/config.py / quickunzip/core.py / quickunzip/gui_main.py / build.py

import os
import sys

# APP_DIR: 用户可见的程序目录(exe 所在目录;源码运行时为仓库根目录)。
# BUNDLE_DIR: 随程序分发的资源(7z.exe / UnRAR.exe / config.example.json)所在目录;
#             PyInstaller onedir 模式下为 exe 旁的 _internal/。
if getattr(sys, 'frozen', False):
    APP_DIR = os.path.dirname(os.path.abspath(sys.executable))
    BUNDLE_DIR = getattr(sys, '_MEIPASS', APP_DIR)
else:
    APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    BUNDLE_DIR = APP_DIR

TOOL_DIRS = list(dict.fromkeys([BUNDLE_DIR, APP_DIR]))
CONFIG_FILE = os.path.join(APP_DIR, 'config.json')
CONFIG_EXAMPLE_FILES = [os.path.join(d, 'config.example.json') for d in TOOL_DIRS]

FAIL_LOG_NAME = '解压失败日志.txt'
FAIL_LOG_FILE = os.path.join(APP_DIR, FAIL_LOG_NAME)

# 窗口图标: 打包时 build.py 把多尺寸 ico 放进 _internal/;源码运行时用 assets/ 里的原图
ICON_FILE = next((p for p in (os.path.join(BUNDLE_DIR, 'QuickUnzip.ico'),
                              os.path.join(BUNDLE_DIR, 'icon_app.ico'),
                              os.path.join(APP_DIR, 'assets', 'icon_app.ico'))
                  if os.path.isfile(p)), None)
