# tests/test_extract_rules.py — 解压规则测试：各种目录结构不丢文件、RAR 优先（改后缀 / 无后缀 / 失败回退 7z）、非压缩包只试一次、出错不中断整批
#
# 用法：python -m unittest discover -s tests -t .
# 配套文件：quickunzip/core.py / tests/test_core.py / UnRAR.exe / 7z.exe

import os
import struct
import subprocess
import tempfile
import unittest
import zlib

from quickunzip import core


def _rar_block(head_type, flags, body):
    data = struct.pack('<BHH', head_type, flags, 7 + len(body)) + body
    return struct.pack('<H', zlib.crc32(data) & 0xFFFF) + data


def stored_rar(name, data):
    """按 RAR 4 格式生成只含一个"存储"文件的最小 rar(无需 rar.exe)。"""
    fname = name.encode('ascii')
    body = struct.pack('<IIBIIBBHI', len(data), len(data), 2, zlib.crc32(data) & 0xFFFFFFFF,
                       0x5A210000, 20, 0x30, len(fname), 0x20) + fname
    return (b'Rar!\x1a\x07\x00' + _rar_block(0x73, 0, b'\x00' * 6)
            + _rar_block(0x74, 0x8000, body) + data + _rar_block(0x7B, 0x4000, b''))


def first_rar_volume(name, data):
    """多卷 rar 的第一卷(.partN.rar 命名),文件数据延续到第二卷。"""
    fname = name.encode('ascii')
    body = struct.pack('<IIBIIBBHI', len(data), len(data) * 2, 2, 0,
                       0x5A210000, 20, 0x30, len(fname), 0x20) + fname
    return (b'Rar!\x1a\x07\x00' + _rar_block(0x73, 0x0001 | 0x0010 | 0x0100, b'\x00' * 6)
            + _rar_block(0x74, 0x8000 | 0x0002, body) + data)


class RulesTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        core.init_tools()

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = self._tmp.name
        self.src = os.path.join(self.root, 'src')
        os.makedirs(self.src)
        self.out = os.path.join(self.root, 'out')
        self.log = os.path.join(self.root, 'fail.log')
        self.calls = []
        original = core._run_tool

        self.cmds = []

        def spy(cmd, **kwargs):
            self.calls.append(os.path.basename(cmd[0]).lower())
            self.cmds.append(cmd)
            return original(cmd, **kwargs)
        core._run_tool = spy
        self.addCleanup(setattr, core, '_run_tool', original)

    def tearDown(self):
        self._tmp.cleanup()

    def zip_of(self, name, layout, password=None, folder=None):
        stage = tempfile.mkdtemp(dir=self.root)
        for rel, data in layout.items():
            p = os.path.join(stage, rel)
            os.makedirs(os.path.dirname(p), exist_ok=True)
            with open(p, 'wb') as f:
                f.write(data)
        out = os.path.join(folder or self.src, name)
        cmd = [core.SEVEN_ZIP_EXE, 'a', out, os.path.join(stage, '*')]
        if password:
            cmd.append(f'-p{password}')
        subprocess.run(cmd, check=True, capture_output=True)
        return out

    def run_unified(self, *inputs, passwords=('pw',)):
        r = core.Extractor(list(passwords), fail_log=self.log).run(
            core.plan_unified(list(inputs), self.out))
        return r

    def out_files(self):
        return sorted(os.path.relpath(os.path.join(a, f), self.out).replace('\\', '/')
                      for a, _, fs in os.walk(self.out) for f in fs)


class LayoutTests(RulesTestCase):
    def test_two_folders(self):
        z = self.zip_of('two.zip', {'A/1.jpg': b'a', 'B/2.jpg': b'b'})
        r = self.run_unified(z)
        self.assertEqual((r.succeeded, r.failed), (1, 0))
        self.assertEqual(self.out_files(), ['two/A/1.jpg', 'two/B/2.jpg'])

    def test_folder_and_note(self):
        z = self.zip_of('pack.zip', {'pics/1.jpg': b'a', 'readme.txt': b'r'})
        self.run_unified(z)
        self.assertEqual(self.out_files(), ['pack/pics/1.jpg', 'pack/readme.txt'])

    def test_archive_inside_folder(self):
        inner = self.zip_of('inner.zip', {'set/1.jpg': b'a'}, folder=self.root)
        with open(inner, 'rb') as f:
            outer = self.zip_of('outer.zip', {'wrap/inner.zip': f.read()})
        r = self.run_unified(outer)
        self.assertEqual(r.failed, 0, r.failures)
        self.assertEqual(self.out_files(), ['set/1.jpg'])

    def test_archive_with_cover_image(self):
        inner = self.zip_of('inner.zip', {'set/1.jpg': b'a'}, folder=self.root)
        with open(inner, 'rb') as f:
            outer = self.zip_of('outer.zip', {'inner.zip': f.read(), 'cover.jpg': b'c'})
        self.run_unified(outer)
        self.assertEqual(self.out_files(), ['outer/cover.jpg', 'set/1.jpg'])

    def test_inner_unknown_file_kept_without_failure(self):
        z = self.zip_of('movie.zip', {'movie.mkv': b'm', 'info.nfo': b'n'})
        r = self.run_unified(z)
        self.assertEqual((r.succeeded, r.failed), (1, 0), r.failures)
        self.assertEqual(self.out_files(), ['movie/info.nfo', 'movie/movie.mkv'])

    def test_inner_locked_archive_kept_and_reported(self):
        inner = self.zip_of('locked.zip', {'x/1.jpg': b'a'}, password='other', folder=self.root)
        with open(inner, 'rb') as f:
            outer = self.zip_of('outer.zip', {'locked.zip': f.read(), 'note.txt': b'n'})
        r = self.run_unified(outer)
        self.assertEqual(r.failed, 1)
        self.assertEqual(r.failures[0][0], core.REASON_BAD_PASSWORD)
        self.assertEqual(self.out_files(), ['outer/locked.zip', 'outer/note.txt'])

    def test_failed_inner_layer_keeps_source_with_delete_on(self):
        inner = self.zip_of('locked.zip', {'x/1.jpg': b'a'}, password='other', folder=self.root)
        with open(inner, 'rb') as f:
            outer = self.zip_of('outer.zip', {'locked.zip': f.read()})
        trashed = []
        core.Extractor(['pw'], delete_source=True, fail_log=self.log,
                       trash=lambda fs: trashed.extend(fs) or True).run(core.plan_unified([outer], self.out))
        self.assertEqual(trashed, [])


class RarFirstTests(RulesTestCase):
    def write_rar(self, name):
        p = os.path.join(self.src, name)
        with open(p, 'wb') as f:
            f.write(stored_rar('hello.txt', b'hello\n'))
        return p

    def test_disguised_rar_uses_unrar_and_restores_name(self):
        p = self.write_rar('资料.666')
        r = self.run_unified(p)
        self.assertEqual((r.succeeded, r.failed), (1, 0), r.failures)
        self.assertEqual(self.calls[0], 'unrar.exe')
        self.assertNotIn('7z.exe', self.calls)
        self.assertEqual(os.listdir(self.src), ['资料.666'])
        self.assertEqual(self.out_files(), ['资料/hello.txt'])

    def test_original_never_renamed(self):
        p = self.write_rar('资料.666')
        self.run_unified(p)
        archive_arg = self.cmds[0][-2]
        self.assertTrue(archive_arg.endswith('资料.666.rar'))
        self.assertNotEqual(os.path.dirname(archive_arg), self.src)
        self.assertFalse(os.path.exists(os.path.join(self.out, core.WORK_DIR_NAME)))

    def test_crash_during_unrar_leaves_original_intact(self):
        p = self.write_rar('资料.666')
        original = core.extract_with_unrar
        core.extract_with_unrar = lambda *a: (_ for _ in ()).throw(RuntimeError('boom'))
        self.addCleanup(setattr, core, 'extract_with_unrar', original)
        r = self.run_unified(p)
        self.assertEqual(r.failed, 1)
        self.assertEqual(os.listdir(self.src), ['资料.666'])
        self.assertFalse(os.path.exists(os.path.join(self.out, core.WORK_DIR_NAME)))

    def test_rar_without_extension(self):
        p = self.write_rar('data')
        r = self.run_unified(p)
        self.assertEqual(r.failed, 0, r.failures)
        self.assertEqual(os.listdir(self.src), ['data'])

    def test_rar_named_file_that_is_zip_falls_back_to_7z(self):
        z = self.zip_of('real.zip', {'d/1.jpg': b'a'})
        fake = os.path.join(self.src, 'fake.rar')
        os.rename(z, fake)
        r = self.run_unified(fake)
        self.assertEqual(r.failed, 0, r.failures)
        self.assertEqual(self.calls, ['unrar.exe', '7z.exe'])
        self.assertEqual(self.out_files(), ['d/1.jpg'])

    def test_rar_missing_last_volume(self):
        p = os.path.join(self.src, 'big.part1.rar')
        with open(p, 'wb') as f:
            f.write(first_rar_volume('big.bin', b'x' * 40))
        r = self.run_unified(p)
        self.assertEqual(r.failures[0][0], core.REASON_MISSING_VOLUME)
        self.assertEqual(self.calls.count('unrar.exe'), 1)

    def test_truncated_rar(self):
        p = os.path.join(self.src, 'cut.rar')
        with open(p, 'wb') as f:
            f.write(stored_rar('hello.txt', b'hello\n')[:60])
        r = self.run_unified(p)
        self.assertEqual(r.failures[0][0], core.REASON_TRUNCATED)

    def test_plain_zip_never_touches_unrar(self):
        z = self.zip_of('a.zip', {'d/1.jpg': b'a'})
        self.run_unified(z)
        self.assertNotIn('unrar.exe', self.calls)


class EarlyStopAndRobustnessTests(RulesTestCase):
    def test_non_archive_tried_once(self):
        p = os.path.join(self.src, 'photo.jpg')
        with open(p, 'wb') as f:
            f.write(b'\xff\xd8 not an archive')
        r = self.run_unified(p, passwords=[f'p{i}' for i in range(20)])
        self.assertEqual(self.calls, ['7z.exe'])
        self.assertEqual(r.failures, [(core.REASON_UNSUPPORTED, 'photo.jpg')])

    def test_bad_output_dir_does_not_abort_batch(self):
        z = self.zip_of('a.zip', {'d/1.jpg': b'a'})
        blocker = os.path.join(self.root, 'blocker')
        open(blocker, 'w').close()
        r = core.Extractor([], fail_log=self.log).run(
            [(z, os.path.join(blocker, 'sub')), (z, self.out)])
        self.assertEqual((r.succeeded, r.failed), (1, 1))
        self.assertEqual(self.out_files(), ['d/1.jpg'])

    def test_unwritable_log_still_returns_result(self):
        p = os.path.join(self.src, 'photo.jpg')
        open(p, 'wb').close()
        r = core.Extractor([], fail_log=os.path.join(self.root, 'no', 'such', 'dir', 'log.txt')).run(
            core.plan_unified([p], self.out))
        self.assertEqual(r.failed, 1)
        self.assertIsNone(r.log_file)


class LongPathAndDashPasswordTests(RulesTestCase):
    def test_deep_archive_extracts_and_work_dir_is_cleaned(self):
        import zipfile
        z = os.path.join(self.src, 'deep.zip')
        with zipfile.ZipFile(z, 'w') as zf:
            zf.writestr('/'.join(['d' * 60] * 4) + '/file.txt', b'x')
        r = self.run_unified(z)
        self.assertEqual(r.failed, 0, r.failures)
        self.assertFalse(os.path.exists(os.path.join(self.out, core.WORK_DIR_NAME)))

    def test_path_too_long_errors_are_named(self):
        long_name = 'C:\\' + 'd' * 300
        e206 = OSError(0, 'x', 'C:\\short')
        e206.winerror = core.ERROR_FILENAME_EXCED_RANGE
        e3 = FileNotFoundError(2, 'x', long_name)
        e3.winerror = core.ERROR_PATH_NOT_FOUND
        e3_short = FileNotFoundError(2, 'x', 'C:\\short')
        e3_short.winerror = core.ERROR_PATH_NOT_FOUND
        self.assertEqual(core._describe_failure(e206, 'a.zip')[0], core.REASON_PATH_TOO_LONG)
        self.assertEqual(core._describe_failure(e3, 'a.zip')[0], core.REASON_PATH_TOO_LONG)
        self.assertNotEqual(core._describe_failure(e3_short, 'a.zip')[0], core.REASON_PATH_TOO_LONG)

    @unittest.skipUnless(os.name == 'nt', 'Windows only')
    def test_extended_prefix(self):
        self.assertEqual(core._extended('C:/a/b'), '\\\\?\\C:\\a\\b')
        self.assertEqual(core._extended('\\\\nas\\share\\x'), '\\\\?\\UNC\\nas\\share\\x')
        self.assertEqual(core._extended('\\\\?\\C:\\a'), '\\\\?\\C:\\a')

    def test_dash_password_goes_to_7z_for_rar(self):
        p = os.path.join(self.src, 'x.rar')
        with open(p, 'wb') as f:
            f.write(stored_rar('hello.txt', b'hello\n'))
        r = self.run_unified(p, passwords=['-'])
        self.assertEqual(r.failed, 0, r.failures)
        self.assertEqual(self.calls[0], '7z.exe')
        self.assertIn('-p-', self.cmds[0])


class WorkDirTests(RulesTestCase):
    def test_stale_dirs_cleaned_live_ones_kept(self):
        import subprocess as sp
        import sys
        work = os.path.join(self.out, core.WORK_DIR_NAME)
        stale = os.path.join(work, '999999-dead', 'half')
        old_format = os.path.join(work, 'ab12cd34')
        live_proc = sp.Popen([sys.executable, '-c', 'import time; time.sleep(30)'])
        self.addCleanup(live_proc.wait)
        self.addCleanup(live_proc.kill)
        live = os.path.join(work, f'{live_proc.pid}-beef', 'busy')
        for d in (stale, old_format, live):
            os.makedirs(d)
        z = self.zip_of('a.zip', {'d/1.jpg': b'a'})
        r = self.run_unified(z)
        self.assertEqual(r.failed, 0, r.failures)
        self.assertEqual(os.listdir(work), [f'{live_proc.pid}-beef'])

    def test_work_dir_removed_after_run(self):
        z = self.zip_of('a.zip', {'d/1.jpg': b'a'})
        self.run_unified(z)
        self.assertFalse(os.path.exists(os.path.join(self.out, core.WORK_DIR_NAME)))


class VolumeSelectionTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.d = self._tmp.name
        for name in ('m.part1.rar', 'm.part2.rar', 'm.part3.rar', 'v.7z.001', 'v.7z.002', 'a.zip'):
            open(os.path.join(self.d, name), 'w').close()

    def tearDown(self):
        self._tmp.cleanup()

    def p(self, name):
        return os.path.join(self.d, name)

    def tasks(self, *names):
        return [os.path.basename(t) for t, _ in core.plan_in_place([self.p(n) for n in names])]

    def test_later_volume_maps_to_first_on_disk(self):
        self.assertEqual(self.tasks('m.part2.rar'), ['m.part1.rar'])
        self.assertEqual(self.tasks('m.part3.rar', 'm.part2.rar'), ['m.part1.rar'])
        self.assertEqual(self.tasks('v.7z.002'), ['v.7z.001'])

    def test_missing_first_volume_keeps_lowest_present(self):
        os.remove(self.p('m.part1.rar'))
        self.assertEqual(self.tasks('m.part3.rar'), ['m.part2.rar'])

    def test_drop_claimed_skips_rest_of_set_in_later_batches(self):
        claimed = set()
        first = core.drop_claimed(core.plan_in_place([self.p('m.part1.rar'), self.p('a.zip')]), claimed)
        self.assertEqual(sorted(os.path.basename(t) for t, _ in first), ['a.zip', 'm.part1.rar'])
        later = core.drop_claimed(core.plan_in_place([self.p('m.part2.rar'), self.p('m.part3.rar')]), claimed)
        self.assertEqual(later, [])
        other = core.drop_claimed(core.plan_in_place([self.p('v.7z.002')]), claimed)
        self.assertEqual([os.path.basename(t) for t, _ in other], ['v.7z.001'])

    def test_drop_claimed_covers_folder_requests(self):
        claimed = set()
        core.drop_claimed(core.plan_in_place([self.p('m.part2.rar')]), claimed)
        rest = core.drop_claimed(core.plan_in_place([self.d]), claimed)
        self.assertEqual(sorted(os.path.basename(t) for t, _ in rest), ['a.zip', 'v.7z.001'])


class LegacyVolumeTests(unittest.TestCase):
    def test_grouping_and_source_files(self):
        with tempfile.TemporaryDirectory() as d:
            for name in ('a.rar', 'a.r00', 'a.r01', 'b.zip', 'b.z01', 'c.r00', 'x.zip'):
                open(os.path.join(d, name), 'w').close()
            tasks = sorted(os.path.basename(t) for t in core.collect_tasks(d))
            self.assertEqual(tasks, ['a.rar', 'b.zip', 'c.r00', 'x.zip'])
            only_part = core._group_split_volumes([os.path.join(d, 'a.r01')])
            self.assertEqual([os.path.basename(p) for p in only_part], ['a.rar'])
            self.assertEqual([os.path.basename(p) for p in core.source_files(os.path.join(d, 'a.rar'))],
                             ['a.rar', 'a.r00', 'a.r01'])
            self.assertEqual([os.path.basename(p) for p in core.source_files(os.path.join(d, 'x.zip'))],
                             ['x.zip'])


if __name__ == '__main__':
    unittest.main()
