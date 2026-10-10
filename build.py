# build.py — 用 PyInstaller 打包 onedir 版 exe，并生成发布目录 dist/QuickUnzip/ 与发布包 dist/QuickUnzip-<version>.zip
#
# 用法：pip install -r requirements-dev.txt；python build.py [--version v1.0.0]
# 配套文件：jieya.py / config.example.json / assets/icon_app.ico / requirements-dev.txt / .github/workflows/release.yml

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
# 程序图标源文件;打包时生成多尺寸 ico(资源管理器小图标需要 16/32/48 px)
ICON_SRC = os.path.join('assets', 'icon_app.ico')
ICON_SIZES = [16, 24, 32, 48, 64, 128, 256]


def _git_version():
    """取 git describe 作为版本号;不在 git 仓库或没装 git 时返回 'dev'。"""
    try:
        out = subprocess.run(
            ['git', 'describe', '--tags', '--always', '--dirty'],
            cwd=ROOT, capture_output=True, text=True, check=True)
        return out.stdout.strip() or 'dev'
    except (OSError, subprocess.CalledProcessError):
        return 'dev'


def make_icon():
    """把 ICON_SRC 转成含 ICON_SIZES 各尺寸的 ico 放到 build/ 下,返回其路径。

    源 ico 只有单一大尺寸时,Windows 缩到 16/32 px 显示会发糊;
    没装 Pillow 时退回直接用源文件。
    """
    src = os.path.join(ROOT, ICON_SRC)
    try:
        from PIL import Image
    except ImportError:
        print("未安装 Pillow,直接使用原始图标(小尺寸显示可能发糊)")
        return src
    out_dir = os.path.join(BUILD_DIR, 'icon')
    os.makedirs(out_dir, exist_ok=True)
    out = os.path.join(out_dir, f'{APP_NAME}.ico')
    with Image.open(src) as img:
        img = img.convert('RGBA')
        side = max(img.size)
        if img.size != (side, side):
            canvas = Image.new('RGBA', (side, side), (0, 0, 0, 0))
            canvas.paste(img, ((side - img.width) // 2, (side - img.height) // 2))
            img = canvas
        img = img.resize((256, 256), Image.LANCZOS)
        img.save(out, format='ICO', sizes=[(n, n) for n in ICON_SIZES])
    return out


def run_pyinstaller(icon):
    try:
        import PyInstaller.__main__
    except ImportError:
        sys.exit("未安装 PyInstaller,请先执行: pip install -r requirements-dev.txt")

    # spec 文件放在 build/ 下,PyInstaller 会相对 spec 目录解析路径
    args = [
        os.path.join(ROOT, 'jieya.py'),
        '--name', APP_NAME,
        '--onedir',
        '--windowed',
        '--noconfirm',
        '--clean',
        '--distpath', DIST_DIR,
        '--workpath', BUILD_DIR,
        '--specpath', BUILD_DIR,
        '--icon', icon,
    ]
    for name in BUNDLED_BINARIES:
        args += ['--add-binary', f"{os.path.join(ROOT, name)}{os.pathsep}."]
    for name in BUNDLED_DATA:
        args += ['--add-data', f"{os.path.join(ROOT, name)}{os.pathsep}."]
    args +=['--add-data', f"{icon}{os.pathsep}."]
    PyInstaller.__main__.run(args)


def assemble_release(version):
    """在 dist/QuickUnzip/ 中补齐用户可见文件,并打成 zip。"""
    shutil.copyfile(os.path.join(ROOT, 'config.example.json'),
                    os.path.join(APP_DIST, 'config.json'))
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

    missing = [n for n in BUNDLED_BINARIES + BUNDLED_DATA + SIDE_FILES + [ICON_SRC]
               if not os.path.isfile(os.path.join(ROOT, n))]
    if missing:
        sys.exit(f"缺少文件: {', '.join(missing)}")

    run_pyinstaller(make_icon())
    zip_path = assemble_release(version)
    print(f"\n程序目录: {APP_DIST}")
    print(f"发布包:   {zip_path}")


if __name__ == '__main__':
    main()
