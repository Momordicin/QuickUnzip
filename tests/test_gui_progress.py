# tests/test_gui_progress.py — 进度窗口测试：右键多选的一整套分卷分几批到达时只解压一次，不误报失败
#
# 用法：python -m unittest discover -s tests -t .
# 配套文件：quickunzip/gui_progress.py / quickunzip/core.py

import json
import os
import queue
import subprocess
import tempfile
import time
import tkinter as tk
import unittest

from quickunzip import core, gui_progress, paths, single_instance


class SplitBatchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        core.init_tools()

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root_dir = self._tmp.name
        saved = (paths.CONFIG_FILE, paths.FAIL_LOG_FILE)
        paths.CONFIG_FILE = os.path.join(self.root_dir, 'config.json')
        paths.FAIL_LOG_FILE = os.path.join(self.root_dir, 'fail.log')
        self.addCleanup(lambda: setattr(paths, 'CONFIG_FILE', saved[0]))
        self.addCleanup(lambda: setattr(paths, 'FAIL_LOG_FILE', saved[1]))
        with open(paths.CONFIG_FILE, 'w', encoding='utf-8') as f:
            json.dump({"passwords": []}, f)

        stage = os.path.join(self.root_dir, 'stage', 'big')
        os.makedirs(stage)
        with open(os.path.join(stage, 'data.bin'), 'wb') as f:
            f.write(os.urandom(30000))
        self.src = os.path.join(self.root_dir, 'dl')
        os.makedirs(self.src)
        subprocess.run([core.SEVEN_ZIP_EXE, 'a', os.path.join(self.src, 'big.7z'), stage, '-v12k'],
                       check=True, capture_output=True)
        self.volumes = sorted(os.listdir(self.src))

    def tearDown(self):
        self._tmp.cleanup()

    def test_volumes_arriving_in_separate_batches_extract_once(self):
        self.assertEqual(len(self.volumes), 3)
        inbox = queue.Queue()
        root = tk.Tk()
        root.withdraw()
        win = gui_progress.ProgressWindow(root, single_instance.Standalone(), inbox,
                                          [os.path.join(self.src, self.volumes[0])])

        def pump(seconds):
            end = time.monotonic() + seconds
            while time.monotonic() < end:
                try:
                    root.update()
                except tk.TclError:
                    return
                time.sleep(0.02)

        try:
            pump(gui_progress.DEBOUNCE_S + 0.3)
            for name in self.volumes[1:]:
                inbox.put(('paths', [os.path.join(self.src, name)]))
                pump(gui_progress.DEBOUNCE_S + 0.3)
            deadline = time.monotonic() + 20
            while (win.busy or win.pending) and time.monotonic() < deadline:
                pump(0.1)
            self.assertEqual((win.total, win.ok, win.failed), (1, 1, 0))
        finally:
            try:
                root.destroy()
            except tk.TclError:
                pass
        dirs = [d for d in os.listdir(self.src) if os.path.isdir(os.path.join(self.src, d))]
        self.assertEqual(dirs, ['big'])
        self.assertFalse(os.path.exists(paths.FAIL_LOG_FILE))


if __name__ == '__main__':
    unittest.main()
