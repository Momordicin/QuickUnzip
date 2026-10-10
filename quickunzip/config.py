# quickunzip/config.py — 读写 config.json：旧配置转换、缺省字段补齐、输入文件夹→输出文件夹历史映射与默认输出路径规则
#
# 用法：from quickunzip.config import Config, UserError, norm_path；cfg = Config.load()；cfg.suggest_output(inputs)；cfg.record_run(inputs, output)
# 配套文件：config.example.json / quickunzip/paths.py / quickunzip/gui_main.py / tests/test_config.py

import copy
import json
import os
import tempfile
import time

from . import paths


DEFAULT_OUTPUT_NAME = '已解压'

DEFAULT_CONFIG = {
    "passwords": [],
    "garbage_list": [],
    "history": [],
    "last_extract_path": "",
    "context_menu": {"file": True, "folder": True, "background": True},
    "post_process": {"purge_garbage": True, "rename": True, "delete_source": False},
    "warned_shared_folder": False,
}

# 旧版(单个 file_path → extract_path)字段;读到时转成第一条 history 后删除
LEGACY_KEYS = ('file_path', 'extract_path')


class UserError(Exception):
    """面向用户的错误:message 直接展示,不带堆栈。"""


def norm_path(p):
    """展开 ~ 与环境变量并转成规范化的绝对路径。"""
    return os.path.normpath(os.path.abspath(os.path.expandvars(os.path.expanduser(p))))


def _same_path(a, b):
    return os.path.normcase(norm_path(a)) == os.path.normcase(norm_path(b))


def _now():
    return time.strftime('%Y-%m-%d %H:%M:%S')


def _fill_defaults(data):
    """补齐缺失字段,并丢掉格式不对的历史条目。返回是否有改动。"""
    changed = False
    for key, default in DEFAULT_CONFIG.items():
        if key not in data:
            data[key] = copy.deepcopy(default)
            changed = True
        elif isinstance(default, dict):
            if not isinstance(data[key], dict):
                raise UserError(f'config.json 中 "{key}" 必须是 {{ ... }} 对象。')
            for sub, sub_default in default.items():
                if sub not in data[key]:
                    data[key][sub] = sub_default
                    changed = True
    if not isinstance(data['last_extract_path'], str):
        data['last_extract_path'] = ''
        changed = True
    for key in ('passwords', 'garbage_list'):
        if any(not isinstance(v, str) for v in data[key]):
            data[key] = [str(v) for v in data[key]]
            changed = True
    good = [h for h in data['history']
            if isinstance(h, dict) and h.get('file_path') and h.get('extract_path')]
    if len(good) != len(data['history']):
        data['history'] = good
        changed = True
    return changed


def _migrate_legacy(data, base_dir):
    """旧版的 file_path / extract_path 一对 → 第一条 history。返回是否有改动。

    旧版相对路径相对程序目录解析;输入文件夹不存在的(例如示例里从没用过的 '待解压')不转。
    """
    if not any(k in data for k in LEGACY_KEYS):
        return False
    src = data.pop('file_path', None)
    dst = data.pop('extract_path', None)
    if src and dst:
        src = norm_path(os.path.join(base_dir, src))
        dst = norm_path(os.path.join(base_dir, dst))
        if os.path.isdir(src):
            history = data.setdefault('history', [])
            if not any(_same_path(h.get('file_path', ''), src) for h in history
                       if isinstance(h, dict)):
                history.insert(0, {"file_path": src, "extract_path": dst,
                                   "last_used": _now()})
            if not data.get('last_extract_path'):
                data['last_extract_path'] = dst
    return True


def _read_json(path):
    try:
        with open(path, encoding='utf-8-sig') as f:
            data = json.load(f)
    except json.JSONDecodeError as e:
        raise UserError(
            f"config.json 格式有误(第 {e.lineno} 行第 {e.colno} 列): {e.msg}\n"
            "常见原因: 末尾多了逗号、字符串少了引号、路径里用了单个反斜杠 \\ "
            "(请改成 / 或 \\\\)。")
    if not isinstance(data, dict):
        raise UserError("config.json 最外层必须是 { ... } 对象。")
    for key in ('passwords', 'garbage_list', 'history'):
        if not isinstance(data.get(key, []), list):
            raise UserError(f'config.json 中 "{key}" 必须是列表, 例如 ["a", "b"]。')
    return data


class Config:
    """config.json 的内存副本;改动后调用 save() 落盘。"""

    def __init__(self, data, path):
        self.data = data
        self.path = path

    # ---------- 读写 ----------

    @classmethod
    def load(cls, path=None, example_files=None):
        """读 config.json;不存在时从 config.example.json(或内置默认值)生成。

        旧版字段与缺失字段在这里一次性转换/补齐并写回。
        """
        path = path or paths.CONFIG_FILE
        if example_files is None:
            example_files = paths.CONFIG_EXAMPLE_FILES
        created = False
        if os.path.isfile(path):
            data = _read_json(path)
        else:
            example = next((f for f in example_files if os.path.isfile(f)), None)
            data = _read_json(example) if example else copy.deepcopy(DEFAULT_CONFIG)
            created = True
        changed = _migrate_legacy(data, os.path.dirname(os.path.abspath(path)))
        changed = _fill_defaults(data) or changed
        cfg = cls(data, path)
        if created or changed:
            cfg.save()
        return cfg

    def save(self):
        """先写临时文件再替换,避免写到一半断电/崩溃留下损坏的 config.json。

        临时文件名每次不同,几个进程同时保存时不会互相抢同一个文件。
        """
        folder = os.path.dirname(os.path.abspath(self.path))
        fd, tmp = tempfile.mkstemp(dir=folder, prefix='config.', suffix='.tmp')
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as f:
                json.dump(self.data, f, ensure_ascii=False, indent=4)
            os.replace(tmp, self.path)
        except BaseException:
            try:
                os.remove(tmp)
            except OSError:
                pass
            raise

    # ---------- 简单字段 ----------

    @property
    def passwords(self):
        return [str(p) for p in self.data['passwords']]

    @property
    def garbage_names(self):
        return set(self.data['garbage_list'])

    @property
    def post_process(self):
        return self.data['post_process']

    @property
    def context_menu(self):
        return self.data['context_menu']

    @property
    def last_extract_path(self):
        return self.data['last_extract_path']

    # ---------- 历史映射 ----------

    @property
    def history(self):
        """全部历史,最近使用的在前。"""
        return self.data['history']

    def recent(self, n=3):
        """下拉框显示用的最近 n 条。"""
        return self.history[:n]

    def lookup(self, folder):
        """输入文件夹在历史里 → 它映射的输出路径,否则 None。"""
        for h in self.history:
            if _same_path(h['file_path'], folder):
                return h['extract_path']
        return None

    def remove_history(self, folder):
        """删除一条历史;返回是否删到了。调用方负责 save()。"""
        before = len(self.history)
        self.data['history'] = [h for h in self.history
                                if not _same_path(h['file_path'], folder)]
        return len(self.history) != before

    def _upsert_history(self, folder, output):
        self.remove_history(folder)
        self.history.insert(0, {"file_path": norm_path(folder),
                                "extract_path": norm_path(output),
                                "last_used": _now()})

    def suggest_output(self, inputs):
        """主窗口输出路径的默认值。

        1. 任一输入文件夹(文件则取其所在文件夹)在历史里 → 它上次用的输出路径;
        2. 否则 → 上一次的输出路径;
        3. 都没有 → 第一个输入所在目录下的 '已解压'
           (输入是文件夹时就是该文件夹里面,输入是文件时是文件旁边)。
        """
        folders = []
        for p in inputs:
            p = norm_path(p)
            folders.append(p if os.path.isdir(p) else os.path.dirname(p))
        for folder in folders:
            hit = self.lookup(folder)
            if hit:
                return hit
        if self.last_extract_path:
            return self.last_extract_path
        if folders:
            return os.path.join(folders[0], DEFAULT_OUTPUT_NAME)
        return ''

    def record_run(self, inputs, output):
        """一次主窗口任务真正执行后调用:文件夹输入写入/覆盖历史,更新上次输出路径并保存。

        文件输入及其所在文件夹不写入历史。
        """
        for p in inputs:
            if os.path.isdir(p):
                self._upsert_history(p, output)
        self.data['last_extract_path'] = norm_path(output)
        self.save()
