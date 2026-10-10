# tests/test_core.py — core.py 的单元测试：用仓库根目录的 7z.exe 现场生成压缩包，覆盖任务规划、解压、后处理、删除源文件、停止
#
# 用法：python -m unittest discover -s tests -t .
# 配套文件：quickunzip/core.py / 7z.exe

import os
import subprocess
import tempfile
import threading
import unittest

from quickunzip import core


class CoreTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        core.init_tools()

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = self._tmp.name
        self.src = os.path.join(self.root, 'src')
        os.makedirs(self.src)
        self.log_file = os.path.join(self.root, 'fail.log')
        self.trashed = []

    def tearDown(self):
        self._tmp.cleanup()

    # ---------- 生成测试数据 ----------

    def make_files(self, folder, names, size=16):
        os.makedirs(folder, exist_ok=True)
        for n in names:
            with open(os.path.join(folder, n), 'wb') as f:
                f.write(os.urandom(size))

    def archive(self, out_path, inputs, password=None, volume=None):
        """把 inputs(文件或文件夹)打成 out_path;volume 如 '4k' 时生成 .001 分卷。"""
        cmd = [core.SEVEN_ZIP_EXE, 'a', out_path] + list(inputs)
        if password:
            cmd.append(f'-p{password}')
        if volume:
            cmd.append(f'-v{volume}')
        subprocess.run(cmd, check=True, capture_output=True)
        return out_path

    def staged(self, name):
        """一个放打包原料的临时目录(不在 src 里)。"""
        d = os.path.join(self.root, 'stage', name)
        os.makedirs(d, exist_ok=True)
        return d

    def simple_zip(self, folder, name, password=None, inner='photos'):
        stage = self.staged(name)
        self.make_files(os.path.join(stage, inner), ['1.jpg', '2.jpg'])
        return self.archive(os.path.join(folder, name), [os.path.join(stage, inner)],
                            password=password)

    def extractor(self, **kwargs):
        def trash(files):
            self.trashed += files
            for f in files:
                os.remove(f)
            return True
        kwargs.setdefault('trash', trash)
        kwargs.setdefault('fail_log', self.log_file)
        kwargs.setdefault('passwords', ['pw'])
        return core.Extractor(**kwargs)


class PlanTests(CoreTestCase):
    def test_unified_collects_everything_into_one_output(self):
        a = os.path.join(self.src, 'a')
        b = os.path.join(self.src, 'b')
        self.make_files(a, ['x.zip', 'y.bin'])
        self.make_files(os.path.join(a, 'sub'), ['z.7z'])
        self.make_files(os.path.join(a, 'sub', 'deep'), ['ignored.zip'])
        self.make_files(b, ['w.rar'])
        out = os.path.join(self.root, 'out')
        jobs = core.plan_unified([a, os.path.join(b, 'w.rar')], out)
        names = sorted(os.path.basename(t) for t, _ in jobs)
        self.assertEqual(names, ['w.rar', 'x.zip', 'y.bin', 'z.7z'])
        self.assertTrue(all(o == out for _, o in jobs))

    def test_unified_skips_output_dir_inside_input(self):
        a = os.path.join(self.src, 'a')
        self.make_files(a, ['x.zip'])
        self.make_files(os.path.join(a, '已解压'), ['old.bin'])
        jobs = core.plan_unified([a], os.path.join(a, '已解压'))
        self.assertEqual([os.path.basename(t) for t, _ in jobs], ['x.zip'])

    def test_in_place_targets(self):
        a = os.path.join(self.src, 'a')
        self.make_files(a, ['x.zip'])
        self.make_files(os.path.join(a, 'sub'), ['y.zip'])
        f = os.path.join(self.src, 'loose.zip')
        self.make_files(self.src, ['loose.zip'])
        jobs = dict((os.path.basename(t), o) for t, o in core.plan_in_place([a, f]))
        self.assertEqual(jobs, {'loose.zip': self.src, 'x.zip': a, 'y.zip': a})

    def test_volumes_grouped_and_deduped(self):
        self.make_files(self.src, ['v.7z.001', 'v.7z.002', 'r.part1.rar', 'r.part2.rar'])
        inputs = [self.src, os.path.join(self.src, 'v.7z.002')]
        jobs = core.plan_unified(inputs, os.path.join(self.root, 'out'))
        self.assertEqual(sorted(os.path.basename(t) for t, _ in jobs),
                         ['r.part1.rar', 'v.7z.001'])


class ExtractTests(CoreTestCase):
    def test_unified_extracts_password_zip(self):
        z = self.simple_zip(self.src, 'album.zip', password='pw')
        out = os.path.join(self.root, 'out')
        result = self.extractor().run(core.plan_unified([z], out))
        self.assertEqual((result.succeeded, result.failed), (1, 0))
        self.assertTrue(os.path.isfile(os.path.join(out, 'photos', '1.jpg')))
        self.assertFalse(os.path.exists(os.path.join(out, core.WORK_DIR_NAME)))
        self.assertTrue(os.path.isfile(z), '默认不删除源文件')
        self.assertIsNone(result.log_file)

    def test_in_place_file_goes_beside_archive(self):
        z = self.simple_zip(self.src, 'album.zip')
        self.extractor().run(core.plan_in_place([z]))
        self.assertTrue(os.path.isfile(os.path.join(self.src, 'photos', '2.jpg')))

    def test_in_place_folder_extracts_into_itself(self):
        folder = os.path.join(self.src, 'downloads')
        os.makedirs(folder)
        self.simple_zip(folder, 'album.zip', inner='set1')
        result = self.extractor().run(core.plan_in_place([folder]))
        self.assertEqual(result.succeeded, 1)
        self.assertTrue(os.path.isfile(os.path.join(folder, 'set1', '1.jpg')))
        self.assertFalse(os.path.exists(os.path.join(folder, '已解压')))
        self.assertFalse(os.path.exists(folder + '_已解压'))

    def test_nested_archive(self):
        inner = self.simple_zip(self.staged('mid'), 'inner.zip')
        disguised = inner[:-4] + '.dat'
        os.rename(inner, disguised)
        outer = self.archive(os.path.join(self.src, 'outer.7z'), [disguised], password='pw')
        out = os.path.join(self.root, 'out')
        result = self.extractor().run(core.plan_unified([outer], out))
        self.assertEqual(result.failed, 0, result.failures)
        self.assertTrue(os.path.isfile(os.path.join(out, 'photos', '1.jpg')))

    def test_non_archive_is_tried_and_logged(self):
        self.make_files(self.src, ['notes.jpg'])
        result = self.extractor().run(core.plan_in_place([self.src]))
        self.assertEqual(result.failed, 1)
        with open(self.log_file, encoding='utf-8') as f:
            line = f.read()
        self.assertIn(core.REASON_UNSUPPORTED, line)
        self.assertIn('notes.jpg', line)

    def test_bad_password_logged_to_given_log(self):
        z = self.simple_zip(self.src, 'locked.zip', password='other')
        result = self.extractor().run(core.plan_in_place([z]))
        self.assertEqual(result.failures, [(core.REASON_BAD_PASSWORD, 'locked.zip')])
        self.assertEqual(result.log_file, self.log_file)

    def test_post_processing_only_touches_produced(self):
        stage = self.staged('g')
        self.make_files(os.path.join(stage, 'No.7 set'), ['img_01.jpg', 'img_02.jpg', 'ad.txt'])
        z = self.archive(os.path.join(self.src, 'g.zip'), [os.path.join(stage, 'No.7 set')])
        out = os.path.join(self.root, 'out')
        self.make_files(out, ['ad.txt'])
        result = self.extractor(garbage_names={'ad.txt'}).run(core.plan_unified([z], out))
        leaf = os.path.join(out, 'No.7 set')
        self.assertEqual(sorted(os.listdir(leaf)), ['7 - 01.jpg', '7 - 02.jpg'])
        self.assertTrue(os.path.isfile(os.path.join(out, 'ad.txt')))
        self.assertEqual((result.purged, result.renamed), (1, 2))

    def test_post_processing_switches_off(self):
        stage = self.staged('g')
        self.make_files(os.path.join(stage, 'No.7 set'), ['img_01.jpg', 'ad.txt'])
        z = self.archive(os.path.join(self.src, 'g.zip'), [os.path.join(stage, 'No.7 set')])
        out = os.path.join(self.root, 'out')
        self.extractor(garbage_names={'ad.txt'}, purge=False, rename=False).run(
            core.plan_unified([z], out))
        self.assertEqual(sorted(os.listdir(os.path.join(out, 'No.7 set'))),
                         ['ad.txt', 'img_01.jpg'])


class DeleteSourceTests(CoreTestCase):
    def test_success_deletes_all_volumes(self):
        stage = self.staged('big')
        self.make_files(stage, ['a.jpg'], size=20000)
        first = self.archive(os.path.join(self.src, 'big.7z'), [os.path.join(stage, 'a.jpg')],
                             volume='8k') + '.001'
        vols = sorted(f for f in os.listdir(self.src) if f.startswith('big.7z.'))
        self.assertGreater(len(vols), 1)
        result = self.extractor(delete_source=True).run(
            core.plan_unified([first], os.path.join(self.root, 'out')))
        self.assertEqual(result.failed, 0, result.failures)
        self.assertEqual(sorted(os.path.basename(p) for p in self.trashed), vols)
        self.assertEqual(result.deleted, len(vols))

    def test_failure_keeps_source(self):
        z = self.simple_zip(self.src, 'locked.zip', password='other')
        result = self.extractor(delete_source=True).run(core.plan_in_place([z]))
        self.assertEqual(result.failed, 1)
        self.assertTrue(os.path.isfile(z))
        self.assertEqual(self.trashed, [])

    def test_inner_layer_failure_keeps_source(self):
        bad_inner = self.simple_zip(self.staged('mid'), 'inner.zip', password='nope')
        outer = self.archive(os.path.join(self.src, 'outer.zip'), [bad_inner])
        result = self.extractor(delete_source=True).run(core.plan_in_place([outer]))
        self.assertEqual(result.failed, 1)
        self.assertTrue(os.path.isfile(outer))

    def test_no_recycle_bin_keeps_source(self):
        z = self.simple_zip(self.src, 'album.zip')
        result = self.extractor(delete_source=True, can_trash=lambda _p: False).run(
            core.plan_in_place([z]))
        self.assertEqual(result.succeeded, 1)
        self.assertEqual(self.trashed, [])
        self.assertTrue(os.path.isfile(z))
        self.assertEqual(result.kept_no_recycle_bin, 1)

    @unittest.skipUnless(os.name == 'nt', 'Windows only')
    def test_has_recycle_bin(self):
        self.assertTrue(core.has_recycle_bin(self.src))
        self.assertFalse(core.has_recycle_bin(r'\\server\share\a.zip'))

    def test_disabled_by_default(self):
        self.assertFalse(core.Extractor([]).delete_source)


class StopAndProgressTests(CoreTestCase):
    def test_stop_after_current_task(self):
        for i in range(3):
            self.simple_zip(self.src, f'a{i}.zip')
        stop = threading.Event()
        events = []

        def progress(done, total, current, result):
            events.append((done, total, current))
            stop.set()

        result = self.extractor(stop_event=stop, on_progress=progress).run(
            core.plan_unified([self.src], os.path.join(self.root, 'out')))
        self.assertTrue(result.stopped)
        self.assertEqual((result.succeeded, result.failed, result.skipped), (1, 0, 2))
        self.assertEqual(events[0][:2], (0, 3))
        self.assertEqual(events[-1], (1, 3, None))


class FailLogTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.log = os.path.join(self._tmp.name, 'fail.log')

    def tearDown(self):
        self._tmp.cleanup()

    def lines(self):
        with open(self.log, encoding='utf-8') as f:
            return f.read().splitlines()

    def test_run_writes_details_and_summary(self):
        self.assertIsNone(core.write_failure_log([], self.log, succeeded=2, failed=0))
        self.assertEqual(core.write_failure_log([('坏了', 'a.zip'), ('坏了', 'b.zip')], self.log,
                                                succeeded=1, failed=2), self.log)
        lines = self.lines()
        self.assertTrue(lines[0].endswith('[本次] 成功 2 个 / 失败 0 个'))
        self.assertTrue(lines[1].endswith('[坏了] a.zip | b.zip'))
        self.assertTrue(lines[2].endswith('[本次] 成功 1 个 / 失败 2 个'))

    def test_consolidate_sums_everything_into_one_line(self):
        with open(self.log, 'w', encoding='utf-8') as f:
            f.write('2026-10-01 10:00:00 [密码错误] old1.zip | old2.zip\n'
                    '2026-10-02 10:00:00 [累计] 解压成功 10 个 / 解压失败 3 个\n'
                    '2026-10-09 10:00:00 [坏了] a.zip\n'
                    '2026-10-09 10:00:00 [本次] 成功 5 个 / 失败 1 个\n'
                    '2026-10-10 08:00:00 [本次] 成功 2 个 / 失败 0 个\n')
        self.assertTrue(core.consolidate_log(self.log))
        lines = self.lines()
        self.assertEqual(len(lines), 1)
        self.assertTrue(lines[0].endswith('[累计] 解压成功 17 个 / 解压失败 4 个'), lines[0])
        self.assertFalse(core.consolidate_log(self.log))
        self.assertEqual(os.listdir(self._tmp.name), ['fail.log'])

    def test_consolidate_missing_log(self):
        self.assertFalse(core.consolidate_log(self.log))


class TrashTests(unittest.TestCase):
    @unittest.skipUnless(os.name == 'nt', 'Windows only')
    def test_send_to_trash_removes_file(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, 'quickunzip_trash_test.tmp')
            open(p, 'w').close()
            self.assertTrue(core.send_to_trash([p]))
            self.assertFalse(os.path.exists(p))


if __name__ == '__main__':
    unittest.main()
