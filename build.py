"""自动打包发版

用法:
    pip install -r requirements-dev.txt
    python build.py [--version v1.0.0]

产物:
    dist/QuickUnzip/                  可直接运行的程序目录
        QuickUnzip.exe
        config.json                   由 config.example.json 生成(不会带上开发者本地的 config.json)
        待解压/
        _internal/                    Python 运行时 + 7z.exe / 7z.dll / UnRAR.exe
        README.md / LICENSE
    dist/QuickUnzip-<version>.zip     上面目录的压缩包,用于发布
"""

import argparse
import os
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
APP_NAME = 'QuickUnzip'
DIST_DIR = os.path.join(ROOT, 'dist')
BUILD_DIR = os.path.join(ROOT, 'build')
APP_DIST = os.path.join(DIST_DIR, APP_NAME)

# 打进 _internal/ 的外部工具与模板
BUNDLED_BINARIES = ['7z.exe', '7z.dll', 'UnRAR.exe']
BUNDLED_DATA = ['config.example.json']
# 放在 exe 旁边给用户看的文件
SIDE_FILES = ['README.md', 'LICENSE']


def _git_version():
    """取 git describe 作为版本号;不在 git 仓库或没装 git 时返回 'dev'。"""
    try:
        out = subprocess.run(
            ['git', 'describe', '--tags', '--always', '--dirty'],
            cwd=ROOT, capture_output=True, text=True, check=True)
        return out.stdout.strip() or 'dev'
    except (OSError, subprocess.CalledProcessError):
        return 'dev'


def run_pyinstaller():
    try:
        import PyInstaller.__main__
    except ImportError:
        sys.exit("未安装 PyInstaller,请先执行: pip install -r requirements-dev.txt")

    # spec 文件放在 build/ 下,PyInstaller 会相对 spec 目录解析路径
    args = [
        os.path.join(ROOT, 'jieya.py'),
        '--name', APP_NAME,
        '--onedir',
        '--console',            # 控制台窗口显示进度与汇总
        '--noconfirm',
        '--clean',
        '--distpath', DIST_DIR,
        '--workpath', BUILD_DIR,
        '--specpath', BUILD_DIR,
    ]
    for name in BUNDLED_BINARIES:
        args += ['--add-binary', f"{os.path.join(ROOT, name)}{os.pathsep}."]
    for name in BUNDLED_DATA:
        args += ['--add-data', f"{os.path.join(ROOT, name)}{os.pathsep}."]
    PyInstaller.__main__.run(args)


def assemble_release(version):
    """在 dist/QuickUnzip/ 中补齐用户可见文件,并打成 zip。"""
    shutil.copyfile(os.path.join(ROOT, 'config.example.json'),
                    os.path.join(APP_DIST, 'config.json'))
    os.makedirs(os.path.join(APP_DIST, '待解压'), exist_ok=True)
    for name in SIDE_FILES:
        shutil.copyfile(os.path.join(ROOT, name), os.path.join(APP_DIST, name))

    base = os.path.join(DIST_DIR, f"{APP_NAME}-{version}")
    zip_path = shutil.make_archive(base, 'zip', root_dir=DIST_DIR, base_dir=APP_NAME)
    return zip_path


def main():
    parser = argparse.ArgumentParser(description=f'打包 {APP_NAME} 发布版')
    parser.add_argument('--version', default=None,
                        help='发布包版本号(默认取 git describe)')
    args = parser.parse_args()
    version = args.version or _git_version()

    missing = [n for n in BUNDLED_BINARIES + BUNDLED_DATA + SIDE_FILES
               if not os.path.isfile(os.path.join(ROOT, n))]
    if missing:
        sys.exit(f"缺少文件: {', '.join(missing)}")

    run_pyinstaller()
    zip_path = assemble_release(version)
    print(f"\n程序目录: {APP_DIST}")
    print(f"发布包:   {zip_path}")


if __name__ == '__main__':
    main()
