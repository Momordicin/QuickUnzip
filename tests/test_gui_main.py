# tests/test_gui_main.py — gui_main.py 后台任务的单元测试：不创建窗口，直接调用解压线程函数检查交回界面的事件
#
# 用法：python -m unittest discover -s tests -t .
# 配套文件：quickunzip/gui_main.py

import os
import queue
import subprocess
import tempfile
import threading
import types
import unittest

from quickunzip import core, gui_main
from quickunzip.config import Config


class WorkTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        core.init_tools()

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = self._tmp.name
        stage = os.path.join(self.root, 'stage', 'set')
        os.makedirs(stage)
        with open(os.path.join(stage, '1.jpg'), 'wb') as f:
            f.write(b'x')
        self.src = os.path.join(self.root, 'src')
        os.makedirs(self.src)
        subprocess.run([core.SEVEN_ZIP_EXE, 'a', os.path.join(self.src, 'a.zip'), stage],
                       check=True, capture_output=True)
        self.cfg = Config.load(os.path.join(self.root, 'config.json'), example_files=[])
        self.window = types.SimpleNamespace(events=queue.Queue(), stop_event=threading.Event())

    def tearDown(self):
        self._tmp.cleanup()

    def events(self):
        out = []
        while not self.window.events.empty():
            out.append(self.window.events.get())
        return out

    def test_history_save_failure_still_reports_result(self):
        def broken(*_args):
            raise PermissionError('config.json 被占用')
        self.cfg.record_run = broken
        out = os.path.join(self.root, 'out')
        gui_main.MainWindow._work(self.window, [self.src], out, self.cfg)
        done = [e for e in self.events() if e[0] == 'done']
        self.assertEqual(len(done), 1)
        self.assertEqual(done[0][1].succeeded, 1)
        self.assertIn('config.json 被占用', done[0][2])
        self.assertTrue(os.path.isfile(os.path.join(out, 'set', '1.jpg')))

    def test_normal_run_records_history(self):
        out = os.path.join(self.root, 'out')
        gui_main.MainWindow._work(self.window, [self.src], out, self.cfg)
        done = [e for e in self.events() if e[0] == 'done']
        self.assertIsNone(done[0][2])
        self.assertEqual(self.cfg.lookup(self.src), os.path.normpath(out))


if __name__ == '__main__':
    unittest.main()
