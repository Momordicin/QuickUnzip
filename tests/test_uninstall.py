# tests/test_uninstall.py — uninstall.py 的单元测试：导出密码本，以及在临时程序目录上真实运行删除脚本
#
# 用法：python -m unittest discover -s tests -t .
# 配套文件：quickunzip/uninstall.py

import os
import subprocess
import sys
import tempfile
import time
import unittest

from quickunzip import paths, uninstall


class ExportTests(unittest.TestCase):
    def test_one_password_per_line(self):
        with tempfile.TemporaryDirectory() as d:
            out = os.path.join(d, uninstall.PASSWORD_FILE_NAME)
            uninstall.export_passwords(out, ['a', '中文密码', 'x y'])
            with open(out, encoding='utf-8') as f:
                self.assertEqual(f.read(), 'a\n中文密码\nx y\n')


@unittest.skipUnless(os.name == 'nt', 'Windows only')
class SelfDeleteTests(unittest.TestCase):
    def make_app(self, root):
        app = os.path.join(root, "Quick'Unzip 程序")
        os.makedirs(os.path.join(app, '_internal', 'sub'))
        for name in ('QuickUnzip.exe', 'config.json', 'README.md', 'LICENSE',
                     paths.FAIL_LOG_NAME, os.path.join('_internal', 'sub', 'x.dll')):
            open(os.path.join(app, name), 'w').close()
        os.makedirs(os.path.join(app, '待解压'))
        os.makedirs(os.path.join(app, '已解压', 'done'))
        return app

    def run_script(self, app, pid):
        script = uninstall.build_script(pid, app, uninstall.delete_targets(app))
        path = os.path.join(os.path.dirname(app), 'uninstall.ps1')
        with open(path, 'w', encoding='utf-8-sig', newline='') as f:
            f.write(script)
        subprocess.run(['powershell.exe', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', path],
                       check=True, capture_output=True, timeout=60)
        return path

    def test_deletes_program_and_keeps_user_data(self):
        with tempfile.TemporaryDirectory() as root:
            app = self.make_app(root)
            waiter = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(1.5)'])
            start = time.monotonic()
            script = self.run_script(app, waiter.pid)
            waiter.wait()
            self.assertGreaterEqual(time.monotonic() - start, 1.0)
            self.assertEqual(sorted(os.listdir(app)), sorted(['待解压', '已解压', paths.FAIL_LOG_NAME]))
            self.assertTrue(os.path.isdir(os.path.join(app, '已解压', 'done')))
            self.assertFalse(os.path.exists(script))

    def test_removes_folder_when_nothing_left(self):
        with tempfile.TemporaryDirectory() as root:
            app = os.path.join(root, 'app')
            os.makedirs(os.path.join(app, '_internal'))
            open(os.path.join(app, 'QuickUnzip.exe'), 'w').close()
            self.run_script(app, 999999)
            self.assertFalse(os.path.exists(app))


if __name__ == '__main__':
    unittest.main()
