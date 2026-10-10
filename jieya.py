# jieya.py — 程序入口：按启动参数打开主窗口、进度小窗口，或走开发用的命令行
#
# 用法：jieya.py（主窗口）｜ jieya.py --to-dialog <路径>...（右键"解压至…"）｜ jieya.py --here <路径>... 或 jieya.py <路径>...（右键"智能解压到此处" / 拖到图标）｜ jieya.py --to <输出文件夹> <路径>... ｜ jieya.py --suggest <路径>...（命令行）
# 配套文件：quickunzip/gui_main.py / quickunzip/gui_progress.py / quickunzip/shell_menu.py / quickunzip/core.py / quickunzip/config.py / build.py

import argparse
import os
import sys

from quickunzip import core
from quickunzip.config import Config, UserError


def _open_folder(path):
    """在资源管理器中打开文件夹(仅 Windows;失败静默)。"""
    if hasattr(os, 'startfile'):
        try:
            os.startfile(path)
        except OSError:
            pass


def _print_progress(done, total, current, result):
    if current is not None:
        print(f"[{done + 1}/{total}] {current}  (成功 {result.succeeded} / 失败 {result.failed})")


def _print_summary(result):
    """打印最终汇总:成功/失败数、按原因分组的失败数、日志位置、后处理统计。"""
    print()
    print('=' * 50)
    print(f"完成: 成功 {result.succeeded} 个 / 失败 {result.failed} 个")
    if result.stopped:
        print(f"已停止, 未处理 {result.skipped} 个")
    if result.failures:
        counts = {}
        for reason, _name in result.failures:
            counts[reason] = counts.get(reason, 0) + 1
        for reason, n in counts.items():
            print(f"  [{reason}] {n} 个")
        print(f"失败日志: {result.log_file}")
    if result.purged:
        print(f"清理垃圾文件 {result.purged} 个")
    if result.renamed:
        print(f"重命名文件 {result.renamed} 个")
    if result.deleted:
        print(f"源文件移到回收站 {result.deleted} 个")


def run(inputs, output_dir):
    """统一解压到 output_dir 并记录历史。"""
    cfg = Config.load()
    core.init_tools()
    output_dir = os.path.abspath(output_dir)
    jobs = core.plan_unified(inputs, output_dir)
    if not jobs:
        print("没有可解压的文件。")
        return
    os.makedirs(output_dir, exist_ok=True)
    print(f"解压到: {output_dir}\n")
    extractor = core.Extractor.from_config(cfg, on_progress=_print_progress, log=print)
    result = extractor.run(jobs)
    cfg.record_run(inputs, output_dir)
    _print_summary(result)
    if result.produced:
        _open_folder(output_dir)


def main(argv):
    parser = argparse.ArgumentParser(prog='jieya.py', description='QuickUnzip 批量解压')
    parser.add_argument('inputs', nargs='*', help='压缩包或文件夹')
    parser.add_argument('--to', metavar='输出文件夹',
                        help='全部解压到这个文件夹(并把文件夹输入记入历史)')
    parser.add_argument('--suggest', action='store_true', help='只打印默认输出路径')
    parser.add_argument('--no-pause', action='store_true', help='结束后不等待回车')
    ns = parser.parse_args(argv)

    missing = [p for p in ns.inputs if not os.path.exists(p)]
    if missing:
        raise UserError(f"路径不存在: {', '.join(missing)}")
    if not ns.inputs or not (ns.to or ns.suggest):
        parser.print_help()
        return
    if ns.suggest:
        print(Config.load().suggest_output(ns.inputs))
        return
    run(ns.inputs, ns.to)


def launch_gui(argv):
    """窗口模式的启动参数;不是窗口模式时返回 False。"""
    if not argv:
        from quickunzip import gui_main
        gui_main.run()
    elif argv[0] == '--to-dialog':
        from quickunzip import gui_main
        gui_main.run([p for p in argv[1:] if os.path.exists(p)])
    elif argv[0] == '--here':
        from quickunzip import gui_progress
        gui_progress.run(argv[1:])
    elif all(os.path.exists(a) for a in argv):
        from quickunzip import gui_progress
        gui_progress.run(argv)
    else:
        return False
    return True


if __name__ == "__main__":
    if launch_gui(sys.argv[1:]):
        sys.exit()

    # 双击 / 拖放启动时窗口会在结束后立刻关闭,因此默认停住等回车;
    # 在脚本或终端里调用可加 --no-pause。
    pause = '--no-pause' not in sys.argv and sys.stdin is not None and sys.stdin.isatty()
    try:
        main(sys.argv[1:])
    except UserError as e:
        print(f"\n错误: {e}")
    except KeyboardInterrupt:
        print("\n已取消。")
    except Exception as e:
        print(f"\n意外错误: {type(e).__name__}: {e}")
    if pause:
        try:
            input("\n按回车键退出...")
        except (EOFError, KeyboardInterrupt):
            pass
