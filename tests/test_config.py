import json
import os
import tempfile
import unittest

from quickunzip.config import Config, UserError, DEFAULT_CONFIG


class ConfigTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = self._tmp.name
        self.cfg_path = os.path.join(self.root, 'config.json')

    def tearDown(self):
        self._tmp.cleanup()

    def write_cfg(self, data):
        with open(self.cfg_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False)

    def read_cfg(self):
        with open(self.cfg_path, encoding='utf-8') as f:
            return json.load(f)

    def mkdir(self, *parts):
        p = os.path.join(self.root, *parts)
        os.makedirs(p, exist_ok=True)
        return p

    def touch(self, *parts):
        p = os.path.join(self.root, *parts)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        open(p, 'wb').close()
        return p

    def load(self):
        return Config.load(self.cfg_path, example_files=[])


class LoadTests(ConfigTestCase):
    def test_creates_defaults_when_missing(self):
        cfg = self.load()
        self.assertEqual(self.read_cfg(), DEFAULT_CONFIG)
        self.assertFalse(cfg.post_process['delete_source'])

    def test_creates_from_example(self):
        example = os.path.join(self.root, 'example.json')
        with open(example, 'w', encoding='utf-8') as f:
            json.dump({"passwords": ["x"]}, f)
        cfg = Config.load(self.cfg_path, example_files=[example])
        self.assertEqual(cfg.passwords, ['x'])
        self.assertEqual(self.read_cfg()['history'], [])

    def test_fills_missing_fields_and_keeps_user_values(self):
        self.write_cfg({"passwords": ["a"], "post_process": {"rename": False}})
        cfg = self.load()
        self.assertEqual(cfg.passwords, ['a'])
        self.assertEqual(cfg.post_process,
                         {"rename": False, "purge_garbage": True, "delete_source": False})
        self.assertEqual(cfg.context_menu, DEFAULT_CONFIG['context_menu'])

    def test_bad_json_is_user_error(self):
        with open(self.cfg_path, 'w', encoding='utf-8') as f:
            f.write('{"passwords": ["a",]}')
        with self.assertRaises(UserError):
            self.load()

    def test_drops_malformed_history(self):
        self.write_cfg({"history": [{"file_path": "x"}, "junk",
                                    {"file_path": "a", "extract_path": "b"}]})
        self.assertEqual(len(self.load().history), 1)


class LegacyMigrationTests(ConfigTestCase):
    def test_pair_becomes_first_history_entry(self):
        src = self.mkdir('in')
        dst = os.path.join(self.root, 'out')
        self.write_cfg({"file_path": src, "extract_path": dst, "passwords": ["p"]})
        cfg = self.load()
        data = self.read_cfg()
        self.assertNotIn('file_path', data)
        self.assertNotIn('extract_path', data)
        self.assertEqual(cfg.lookup(src), os.path.normpath(dst))
        self.assertEqual(cfg.last_extract_path, os.path.normpath(dst))
        self.assertEqual(cfg.passwords, ['p'])

    def test_relative_pair_resolves_against_config_dir(self):
        self.mkdir('待解压')
        self.write_cfg({"file_path": "待解压", "extract_path": "已解压"})
        cfg = self.load()
        self.assertEqual(cfg.lookup(os.path.join(self.root, '待解压')),
                         os.path.join(self.root, '已解压'))

    def test_pair_with_missing_input_folder_is_dropped(self):
        self.write_cfg({"file_path": "待解压", "extract_path": "已解压"})
        cfg = self.load()
        self.assertEqual(cfg.history, [])
        self.assertNotIn('file_path', self.read_cfg())


class HistoryTests(ConfigTestCase):
    def test_suggest_falls_back_to_first_item_dir(self):
        cfg = self.load()
        folder = self.mkdir('a')
        f = self.touch('b', 'x.zip')
        self.assertEqual(cfg.suggest_output([folder]), os.path.join(folder, '已解压'))
        self.assertEqual(cfg.suggest_output([f]),
                         os.path.join(os.path.dirname(f), '已解压'))

    def test_suggest_uses_last_output_when_not_in_history(self):
        cfg = self.load()
        a = self.mkdir('a')
        out = self.mkdir('out')
        cfg.record_run([a], out)
        b = self.mkdir('b')
        self.assertEqual(cfg.suggest_output([b]), out)

    def test_suggest_prefers_history_mapping(self):
        cfg = self.load()
        a, b = self.mkdir('a'), self.mkdir('b')
        out_a, out_b = self.mkdir('out_a'), self.mkdir('out_b')
        cfg.record_run([a], out_a)
        cfg.record_run([b], out_b)
        self.assertEqual(cfg.suggest_output([a]), out_a)
        # 文件所在文件夹在历史里也命中
        f = self.touch('a', 'x.zip')
        self.assertEqual(cfg.suggest_output([f]), out_a)

    def test_record_overwrites_mapping_and_moves_to_front(self):
        cfg = self.load()
        a, b = self.mkdir('a'), self.mkdir('b')
        cfg.record_run([a], self.mkdir('o1'))
        cfg.record_run([b], self.mkdir('o2'))
        new_out = self.mkdir('o3')
        cfg.record_run([a], new_out)
        self.assertEqual(len(cfg.history), 2)
        self.assertEqual(cfg.recent(1)[0]['file_path'], os.path.normpath(a))
        self.assertEqual(Config.load(self.cfg_path, []).lookup(a), new_out)

    def test_file_inputs_not_recorded(self):
        cfg = self.load()
        f = self.touch('a', 'x.zip')
        out = self.mkdir('out')
        cfg.record_run([f], out)
        self.assertEqual(cfg.history, [])
        self.assertEqual(cfg.last_extract_path, out)

    def test_recent_and_remove(self):
        cfg = self.load()
        dirs = [self.mkdir(f'd{i}') for i in range(5)]
        for d in dirs:
            cfg.record_run([d], self.mkdir('out'))
        self.assertEqual(len(cfg.history), 5)
        self.assertEqual([h['file_path'] for h in cfg.recent()],
                         [os.path.normpath(d) for d in reversed(dirs[-3:])])
        self.assertTrue(cfg.remove_history(dirs[4]))
        self.assertFalse(cfg.remove_history(dirs[4]))
        self.assertIsNone(cfg.lookup(dirs[4]))


if __name__ == '__main__':
    unittest.main()
