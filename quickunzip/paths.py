"""程序相关路径的唯一来源。

所有程序自身的路径(配置、失败日志、外部工具、右键命令、卸载目标)都只从 APP_DIR /
BUNDLE_DIR 推导,不写进 config.json —— 程序文件夹整体挪走后,下次启动即自动适配。
"""

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

TOOL_DIRS = list(dict.fromkeys([BUNDLE_DIR, APP_DIR]))   # 去重且保持顺序
CONFIG_FILE = os.path.join(APP_DIR, 'config.json')
CONFIG_EXAMPLE_FILES = [os.path.join(d, 'config.example.json') for d in TOOL_DIRS]

FAIL_LOG_NAME = '解压失败日志.txt'
FAIL_LOG_FILE = os.path.join(APP_DIR, FAIL_LOG_NAME)
