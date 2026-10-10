# tests/test_shell_menu.py — shell_menu.py 的单元测试：写在 HKCU\Software\QuickUnzipTest 下，不碰真实的右键菜单
#
# 用法：python -m unittest discover -s tests -t .
# 配套文件：quickunzip/shell_menu.py

import os
import unittest

from quickunzip import shell_menu

TEST_PARENT = r'Software\QuickUnzipTest'
TEST_ROOT = rf'{TEST_PARENT}\Classes'
ALL_ON = {'file': True, 'folder': True, 'background': True}


def _delete_tree(winreg, path):
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, path) as key:
            subs = []
            i = 0
            while True:
                try:
                    subs.append(winreg.EnumKey(key, i))
                except OSError:
                    break
                i += 1
    except FileNotFoundError:
        return
    for sub in subs:
        _delete_tree(winreg, rf'{path}\{sub}')
    winreg.DeleteKey(winreg.HKEY_CURRENT_USER, path)


@unittest.skipUnless(os.name == 'nt', 'Windows only')
class ShellMenuTests(unittest.TestCase):
    def setUp(self):
        import winreg
        self.winreg = winreg
        _delete_tree(winreg, TEST_PARENT)

    def tearDown(self):
        _delete_tree(self.winreg, TEST_PARENT)

    def read(self, key_path):
        return shell_menu._read_entry(self.winreg, TEST_ROOT, key_path)

    def test_apply_writes_all_locations(self):
        self.assertTrue(shell_menu.apply(ALL_ON, TEST_ROOT))
        here = self.read(r'SystemFileAssociations\.zip\shell\QuickUnzip.Here')
        self.assertEqual(here['MUIVerb'], '智能解压到此处')
        self.assertEqual(here['MultiSelectModel'], 'Player')
        self.assertTrue(here['command'].endswith('--here "%1"'))
        to = self.read(r'Directory\Background\shell\QuickUnzip.To')
        self.assertEqual(to['MUIVerb'], '解压至…')
        self.assertTrue(to['command'].endswith('--to-dialog "%V"'))
        for ext in shell_menu.ARCHIVE_EXTS:
            self.assertIsNotNone(self.read(rf'SystemFileAssociations\{ext}\shell\QuickUnzip.Here'))

    def test_apply_is_idempotent(self):
        shell_menu.apply(ALL_ON, TEST_ROOT)
        self.assertFalse(shell_menu.apply(ALL_ON, TEST_ROOT))

    def test_turning_location_off_removes_only_it(self):
        shell_menu.apply(ALL_ON, TEST_ROOT)
        self.assertTrue(shell_menu.apply({'file': False, 'folder': True, 'background': True}, TEST_ROOT))
        self.assertIsNone(self.read(r'SystemFileAssociations\.rar\shell\QuickUnzip.Here'))
        self.assertIsNotNone(self.read(r'Directory\shell\QuickUnzip.Here'))

    def test_changed_command_is_rewritten(self):
        shell_menu.apply(ALL_ON, TEST_ROOT)
        key_path = r'Directory\shell\QuickUnzip.Here'
        stale = dict(self.read(key_path), command='"D:\\old\\QuickUnzip.exe" --here "%1"')
        shell_menu._write_entry(self.winreg, TEST_ROOT, key_path, stale)
        self.assertTrue(shell_menu.apply(ALL_ON, TEST_ROOT))
        self.assertNotIn('D:\\old', self.read(key_path)['command'])

    def test_remove_all_keeps_other_values(self):
        winreg = self.winreg
        shell_menu.apply(ALL_ON, TEST_ROOT)
        ext_key = rf'{TEST_ROOT}\SystemFileAssociations\.zip'
        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, ext_key, 0, winreg.KEY_WRITE) as key:
            winreg.SetValueEx(key, 'PerceivedType', 0, winreg.REG_SZ, 'compressed')
        self.assertTrue(shell_menu.remove_all(TEST_ROOT))
        for key_path in shell_menu.all_entry_keys():
            self.assertIsNone(self.read(key_path))
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, ext_key) as key:
            self.assertEqual(winreg.QueryValueEx(key, 'PerceivedType')[0], 'compressed')


if __name__ == '__main__':
    unittest.main()
