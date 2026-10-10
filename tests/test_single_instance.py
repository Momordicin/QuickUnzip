# tests/test_single_instance.py — single_instance.py 的单元测试：同进程模拟多个实例，以及多个子进程同时启动时只有一个成为服务端
#
# 用法：python -m unittest discover -s tests -t .
# 配套文件：quickunzip/single_instance.py

import os
import queue
import secrets
import subprocess
import sys
import unittest

from quickunzip import single_instance

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CLIENT = """
import sys
sys.path.insert(0, sys.argv[1])
from quickunzip import single_instance
server = single_instance.deliver_or_serve(sys.argv[2], ('paths', [sys.argv[3]]), lambda m: None)
print('served' if server else 'delivered')
"""


@unittest.skipUnless(os.name == 'nt', 'Windows only')
class SingleInstanceTests(unittest.TestCase):
    def setUp(self):
        self.channel = 'test-' + secrets.token_hex(4)
        self.inbox = queue.Queue()

    def get(self, timeout=5):
        return self.inbox.get(timeout=timeout)

    def test_first_serves_second_delivers(self):
        server = single_instance.deliver_or_serve(self.channel, ('paths', ['a']), self.inbox.put)
        self.assertIsInstance(server, single_instance.Server)
        try:
            self.assertIsNone(single_instance.deliver_or_serve(
                self.channel, ('paths', ['b']), lambda m: None))
            self.assertEqual(self.get(), ('paths', ['b']))
        finally:
            server.close()
        self.assertTrue(self.inbox.empty())

    def test_after_close_next_one_serves(self):
        server = single_instance.deliver_or_serve(self.channel, ('paths', []), self.inbox.put)
        server.close()
        again = single_instance.deliver_or_serve(self.channel, ('paths', []), self.inbox.put)
        self.assertIsInstance(again, single_instance.Server)
        again.close()

    def test_many_processes_merge_into_one_server(self):
        server = single_instance.deliver_or_serve(self.channel, ('paths', ['p0']), self.inbox.put)
        try:
            procs = [subprocess.Popen([sys.executable, '-c', CLIENT, ROOT, self.channel, f'p{i}'],
                                      stdout=subprocess.PIPE, text=True)
                     for i in range(1, 9)]
            outputs = [p.communicate(timeout=30)[0].strip() for p in procs]
            self.assertEqual(outputs, ['delivered'] * 8)
            got = sorted(self.get()[1][0] for _ in range(8))
            self.assertEqual(got, sorted(f'p{i}' for i in range(1, 9)))
        finally:
            server.close()

    def test_concurrent_start_elects_single_server(self):
        procs = [subprocess.Popen([sys.executable, '-c', CLIENT + "\nimport time; time.sleep(2)\n"
                                   "server and server.close()", ROOT, self.channel, f'p{i}'],
                                  stdout=subprocess.PIPE, text=True)
                 for i in range(6)]
        outputs = [p.communicate(timeout=30)[0].strip() for p in procs]
        self.assertEqual(outputs.count('served'), 1, outputs)
        self.assertEqual(outputs.count('delivered'), 5, outputs)


if __name__ == '__main__':
    unittest.main()
