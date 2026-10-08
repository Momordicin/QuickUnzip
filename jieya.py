"""批量解压 + 清理 + 重命名工具。

把 file_path 下的压缩包(zip / 7z / rar / .001 分卷 / .partN.rar)递归解压到
extract_path,过程中自动判定何时停止递归,并把产物按"压缩链路最后一层有意义
的名字"放进 output。完成后可清理已知垃圾文件、按文件夹的 No.xxx 编号批量重命
名叶子目录里的文件。

CLI:
    python jieya.py [extract|purge|rename|all]   (不带参时默认 all)
    python jieya.py <压缩包或文件夹> ...          (拖放模式:解压到压缩包旁边)

依赖外部 7z.exe / UnRAR.exe(优先脚本同目录,其次 PATH,最后 Program Files 默认安装路径)。
"""

import argparse
import json
import os
import re
import shutil
import secrets
import subprocess
import sys
import time


# ============================================================
# 配置与常量
# ============================================================

# APP_DIR: 用户可见的程序目录,放 config.json / 待解压 / 已解压(便携式,跟着程序走)。
# BUNDLE_DIR: 随程序分发的资源(7z.exe / UnRAR.exe / config.example.json)所在目录。
# 源码运行时两者都是 jieya.py 所在目录;PyInstaller 打包后 APP_DIR 是 exe 所在目录,
# BUNDLE_DIR 是 PyInstaller 的解包目录(onedir 模式下为 exe 旁的 _internal/)。
if getattr(sys, 'frozen', False):
    APP_DIR = os.path.dirname(os.path.abspath(sys.executable))
    BUNDLE_DIR = getattr(sys, '_MEIPASS', APP_DIR)
else:
    APP_DIR = os.path.dirname(os.path.abspath(__file__))
    BUNDLE_DIR = APP_DIR
TOOL_DIRS = list(dict.fromkeys([BUNDLE_DIR, APP_DIR]))   # 去重且保持顺序
CONFIG_FILE = os.path.join(APP_DIR, 'config.json')
CONFIG_EXAMPLE_FILES = [os.path.join(d, 'config.example.json') for d in TOOL_DIRS]

# config.json 与 config.example.json 都不存在时使用的默认配置。
# 相对路径一律相对 APP_DIR(程序所在目录)解析。
DEFAULT_CONFIG = {
    "file_path": "待解压",
    "extract_path": "已解压",
    "passwords": [],
    "garbage_list": [],
}

# 失败日志文件名(写在待解压目录下;扫描任务时跳过它本身)
FAIL_LOG_NAME = '解压失败日志.txt'

# 以下由 _apply_config() 在启动时填充
file_path = None
extract_path = None
PASSWORDS = []          # extract_file 按顺序尝试,末尾再追加 None 表示"无密码兜底"
GARBAGE_NAMES = set()   # 完整文件名含后缀,解压后递归删除


class UserError(Exception):
    """面向用户的错误:message 直接打印,不带堆栈。"""


def _resolve_path(p):
    """展开 ~ 与环境变量;相对路径相对 APP_DIR 解析。"""
    p = os.path.expandvars(os.path.expanduser(p))
    if not os.path.isabs(p):
        p = os.path.join(APP_DIR, p)
    return os.path.normpath(p)


def _load_config():
    """读 config.json;不存在时从 config.example.json(或内置默认值)自动生成。"""
    if not os.path.isfile(CONFIG_FILE):
        example = next((f for f in CONFIG_EXAMPLE_FILES if os.path.isfile(f)), None)
        if example:
            shutil.copyfile(example, CONFIG_FILE)
        else:
            with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
                json.dump(DEFAULT_CONFIG, f, ensure_ascii=False, indent=4)
        print(f"首次运行,已生成配置文件: {CONFIG_FILE}")
    try:
        with open(CONFIG_FILE, encoding='utf-8-sig') as f:
            cfg = json.load(f)
    except json.JSONDecodeError as e:
        raise UserError(
            f"config.json 格式有误(第 {e.lineno} 行第 {e.colno} 列): {e.msg}\n"
            "常见原因: 末尾多了逗号、字符串少了引号、路径里用了单个反斜杠 \\ "
            "(请改成 / 或 \\\\)。")
    if not isinstance(cfg, dict):
        raise UserError("config.json 最外层必须是 { ... } 对象。")
    for key in ('passwords', 'garbage_list'):
        if not isinstance(cfg.get(key, []), list):
            raise UserError(f'config.json 中 "{key}" 必须是列表, 例如 ["a", "b"]。')
    return cfg


def _apply_config():
    """加载配置并写入模块级全局变量。"""
    global file_path, extract_path, PASSWORDS, GARBAGE_NAMES
    cfg = _load_config()
    file_path = _resolve_path(cfg.get('file_path') or DEFAULT_CONFIG['file_path'])
    extract_path = _resolve_path(cfg.get('extract_path') or DEFAULT_CONFIG['extract_path'])
    PASSWORDS = [str(p) for p in cfg.get('passwords', [])]
    GARBAGE_NAMES = set(cfg.get('garbage_list', []))

# 正则常量
SPLIT_VOL_RE = re.compile(r'\.(\d{3})$')                      # .001 / .002 ...
PART_RAR_RE = re.compile(r'\.part\d+\.rar$', re.IGNORECASE)   # .part1.rar / .part2.rar ...
NO_PATTERN = re.compile(r'[Nn][Oo]\.(\d+)')                   # 文件夹名中的 No.xxx
DIGIT_RUN = re.compile(r'\d+')                                # 任一段连续数字

# 后缀白名单:落在这个集合内的文件视为"已是终态产物,无须再解压";集合外
# (无后缀 / '.7z删除' / '.tar' / ...)一律视为"还要处理",交给 7z.exe 按文件头
# sniff 决定下一步。
NORMAL_EXTS = {
    # images
    '.jpg', '.jpeg', '.png', '.gif', '.webp', '.bmp', '.tiff', '.ico', '.svg',
    # video
    '.mp4', '.mkv', '.avi', '.mov', '.wmv', '.flv', '.webm', '.m4v', '.ts',
    # audio
    '.mp3', '.flac', '.wav', '.ogg', '.m4a', '.aac', '.wma',
    # subtitle
    '.srt', '.ass', '.ssa', '.vtt', '.tts',
    # documents
    '.pdf', '.doc', '.docx', '.xls', '.xlsx', '.ppt', '.pptx',
    '.txt', '.md', '.rtf', '.epub', '.mobi', '.azw3',
    # code / data text
    '.py', '.c', '.cpp', '.h', '.java', '.go', '.rs', '.js', '.rb', '.php',
    '.sh', '.bat', '.ps1',
    '.html', '.css', '.json', '.xml', '.yaml', '.yml', '.toml', '.csv', '.sql',
    # executables / shortcuts
    '.exe', '.dll', '.msi', '.url', '.lnk',
    # fonts
    '.ttf', '.otf', '.woff', '.woff2',
    # misc
    '.iso', '.log', '.ini', '.conf',
}


# ============================================================
# 外部工具调用 (7z.exe + UnRAR.exe)
# ============================================================

def _find_7z_exe():
    """按 TOOL_DIRS → PATH → 默认安装路径 顺序定位 7z.exe,找不到抛错。"""
    for d in TOOL_DIRS:
        local = os.path.join(d, '7z.exe')
        if os.path.isfile(local):
            return local
    found = shutil.which('7z')
    if found:
        return found
    fallback = r'C:\Program Files\7-Zip\7z.exe'
    if os.path.isfile(fallback):
        return fallback
    raise FileNotFoundError(
        f"7z.exe not found in {APP_DIR}, PATH, "
        "or C:\\Program Files\\7-Zip\\7z.exe"
    )


def _find_unrar_exe():
    """按 TOOL_DIRS → PATH → 默认安装路径 顺序定位 UnRAR.exe,找不到抛错。"""
    for d in TOOL_DIRS:
        local = os.path.join(d, 'UnRAR.exe')
        if os.path.isfile(local):
            return local
    found = shutil.which('UnRAR') or shutil.which('unrar')
    if found:
        return found
    fallback = r'C:\Program Files\WinRAR\UnRAR.exe'
    if os.path.isfile(fallback):
        return fallback
    raise FileNotFoundError(
        f"UnRAR.exe not found in {APP_DIR}, PATH, "
        "or C:\\Program Files\\WinRAR\\UnRAR.exe"
    )
    
def _find_bandizip_exe():
    """按 APP_DIR → PATH → 默认安装路径 顺序定位 Bandizip.exe,找不到抛错。"""
    for name in ('Bandizip.exe', 'bandizip.exe'):
        local = os.path.join(APP_DIR, name)
        if os.path.isfile(local):
            return local
    found = shutil.which('Bandizip') or shutil.which('bandizip')
    if found:
        return found
    fallback = r'C:\Program Files\Bandizip\Bandizip.exe'
    if os.path.isfile(fallback):
        return fallback
    raise FileNotFoundError(
        f"Bandizip.exe not found in {APP_DIR}, PATH, "
        "or C:\\Program Files\\Bandizip\\Bandizip.exe"
    )


SEVEN_ZIP_EXE = None
UNRAR_EXE = None


def _init_tools():
    """定位 7z.exe / UnRAR.exe;找不到时转成面向用户的错误。"""
    global SEVEN_ZIP_EXE, UNRAR_EXE
    try:
        SEVEN_ZIP_EXE = _find_7z_exe()
        UNRAR_EXE = _find_unrar_exe()
    except FileNotFoundError as e:
        raise UserError(
            f"找不到解压工具: {e}\n"
            "源码运行: 请确认 7z.exe / 7z.dll / UnRAR.exe 与 jieya.py 在同一目录;\n"
            "exe 版本: 请重新完整解压发布包, 不要单独拷走 QuickUnzip.exe。")


def extract_with_7z(file_path, output_path, password):
    """调 7z.exe 解压。返回 'ok' | 'bad_password' | 'corrupt' | 'fail'。"""
    pw_arg = f'-p{password}' if password else '-p'
    cmd = [SEVEN_ZIP_EXE, 'x', file_path, f'-o{output_path}',
           '-aoa', '-y', pw_arg]
    result = subprocess.run(
        cmd, capture_output=True, text=True,
        encoding='utf-8', errors='replace',
        stdin=subprocess.DEVNULL,
    )
    if result.returncode == 0:
        return 'ok'
    blob = (result.stderr or '') + (result.stdout or '')
    if 'Wrong password' in blob or 'Can not open encrypted archive' in blob:
        return 'bad_password'
    if 'CRC' in blob or 'Data error' in blob:
        return 'corrupt'
    return 'fail'


def extract_with_unrar(file_path, output_path, password):
    """调 UnRAR.exe 解压 .rar / .partN.rar。返回 'ok' | 'bad_password' | 'corrupt' | 'fail'。"""
    pw_arg = f'-p{password}' if password else '-p-'
    out = output_path
    # UnRAR 要求输出路径必须以分隔符结尾,否则它会把路径当成单个目标文件名而非目录。
    if not out.endswith(os.sep) and not out.endswith('/'):
        out = out + os.sep
    cmd = [UNRAR_EXE, 'x', '-o+', '-y', pw_arg, file_path, out]
    result = subprocess.run(
        cmd, capture_output=True, text=True,
        encoding='utf-8', errors='replace',
        stdin=subprocess.DEVNULL,
    )
    if result.returncode == 0:
        return 'ok'
    if result.returncode in (10, 11):
        return 'bad_password'
    if result.returncode == 3:
        return 'corrupt'
    return 'fail'


def _is_rar_file(file_path):
    """判定是否 rar 系列文件(.rar / .partN.rar),用于分派到 UnRAR.exe。"""
    lower = file_path.lower()
    return lower.endswith('.rar') or bool(PART_RAR_RE.search(lower))


REASON_BAD_PASSWORD = '密码错误(密码库中没有匹配的密码)'
REASON_CORRUPT = '压缩包损坏(CRC 或数据错误)'
REASON_UNSUPPORTED = '无法识别的格式或解压失败'


class ExtractError(Exception):
    """解压失败;reason 是用于汇总分组的失败原因(上面的 REASON_* 之一)。"""

    def __init__(self, reason, path):
        super().__init__(f"{reason}: {path}")
        self.reason = reason


def extract_file(file_path, output_path):
    """解压单个压缩包到 output_path:按后缀分派工具、轮询密码,全失败则抛 ExtractError。"""
    os.makedirs(output_path, exist_ok=True)
    extractor = extract_with_unrar if _is_rar_file(file_path) else extract_with_7z

    # 末尾追加 None = "最后一轮不带密码再试一次",兜住完全无密码的包。
    saw_bad_password = False
    for pw in PASSWORDS + [None]:
        status = extractor(file_path, output_path, pw)
        if status == 'ok':
            return
        if status == 'corrupt':
            raise ExtractError(REASON_CORRUPT, file_path)
        if status == 'bad_password':
            saw_bad_password = True
    # 从没报过密码错误 → 大概率根本不是压缩包或格式不支持,而不是密码问题。
    reason = REASON_BAD_PASSWORD if saw_bad_password else REASON_UNSUPPORTED
    raise ExtractError(reason, file_path)


# ============================================================
# 随机目录工作流
# ============================================================

def _new_random_dir(parent):
    """在 parent 下创建并返回一个 8 位 hex 名字的新空目录(碰撞重试)。"""
    os.makedirs(parent, exist_ok=True)
    while True:
        name = secrets.token_hex(4)
        path = os.path.join(parent, name)
        if not os.path.exists(path):
            os.makedirs(path)
            return path


def _safe_output_name(output_dir, name):
    """返回 output_dir 下未占用的目标名:首选 name,冲突时追加 4 位 hex 后缀。"""
    if not os.path.exists(os.path.join(output_dir, name)):
        return name
    while True:
        suffix = secrets.token_hex(2)
        candidate = f"{name}_{suffix}"
        if not os.path.exists(os.path.join(output_dir, candidate)):
            return candidate


def _is_normal_file(name):
    """True iff 文件后缀属于 NORMAL_EXTS 白名单(已经是解压终态)。"""
    _, ext = os.path.splitext(name)
    return ext.lower() in NORMAL_EXTS


def _archive_stem(name):
    """剥掉压缩包式后缀拿到可复用的 stem。

    先剥 .partN.rar / .NNN 整段是因为 os.path.splitext 只剥最后一个点的后缀,
    会把 'foo.part1.rar' 削成 'foo.part1'、把 'foo.001' 直接处理掉但 .partN 残留;
    所以这两种格式必须在 splitext 之前单独整段切掉。
    """
    m = PART_RAR_RE.search(name)
    if m:
        return name[:m.start()]
    m = SPLIT_VOL_RE.search(name)
    if m:
        return name[:m.start()]
    stem, _ = os.path.splitext(name)
    return stem


def _walk_has_abnormal(folder):
    """folder 内(递归)是否含任何非白名单文件 — 即"还需要继续解压/处理"。"""
    for _root, _dirs, files in os.walk(folder):
        for f in files:
            if not _is_normal_file(f):
                return True
    return False


def _group_split_volumes(file_paths):
    """把 .001 / .002 / ... 分卷归组,只保留每组的 .001(其余由 7z 自动联动)。"""
    grouped = {}
    singles = []
    for fp in file_paths:
        m = SPLIT_VOL_RE.search(fp)
        if m:
            base = SPLIT_VOL_RE.sub('', fp)
            grouped.setdefault(base, []).append((int(m.group(1)), fp))
        else:
            singles.append(fp)
    result = list(singles)
    for base, vols in grouped.items():
        vols.sort()
        result.append(vols[0][1])
    return result


# ============================================================
# 智能判定与递归处理
# ============================================================

def classify_output(folder):
    """判定一个随机目录的状态,返回三选一的结果。

    返回:
      ('done_a', sole_subdir_path)   顶层正好 1 子目录、0 文件,且该子目录递归内
                                      全是白名单文件 → 直接把这个子目录搬到 output
      ('done_b', None)               顶层 0 子目录、所有文件都在白名单内 → 用沿用
                                      的 last_name 建一个文件夹包起来搬到 output
      ('need_extract', items)        其它(含混合)→ 对 items 中每项再开新随机目录
                                      递归解压
    """
    entries = os.listdir(folder)
    dirs = [e for e in entries if os.path.isdir(os.path.join(folder, e))]
    files = [e for e in entries if os.path.isfile(os.path.join(folder, e))]

    if len(dirs) == 1 and len(files) == 0:
        sole = os.path.join(folder, dirs[0])
        if not _walk_has_abnormal(sole):
            return ('done_a', sole)

    if len(dirs) == 0 and len(files) > 0:
        if all(_is_normal_file(f) for f in files):
            return ('done_b', None)

    items = [os.path.join(folder, f) for f in files
             if not _is_normal_file(f)]
    items = _group_split_volumes(items)
    return ('need_extract', items)


def _find_deepest_folder_name(folder):
    """沿单链子目录一路向下走,返回链路上最深一层的目录名(供命名沿用)。"""
    deepest = None
    current = folder
    while True:
        try:
            entries = os.listdir(current)
        except OSError:
            break
        subdirs = [e for e in entries
                   if os.path.isdir(os.path.join(current, e))]
        if not subdirs:
            break
        deepest = subdirs[0]
        if len(subdirs) > 1:
            break
        current = os.path.join(current, subdirs[0])
    return deepest


class RunResult:
    """一次批量解压的结果:产出的顶层目录、成功/失败计数、失败明细。"""

    def __init__(self):
        self.produced = []      # 搬进 output_dir 的顶层产物路径
        self.succeeded = 0      # 顶层任务数(含内层全部成功)
        self.failed = 0         # 顶层任务数(本层或任一内层失败)
        self.failures = []      # [(reason, 显示名)]

    def add_failure(self, reason, name):
        self.failures.append((reason, name))

    def merge(self, other):
        self.produced += other.produced
        self.succeeded += other.succeeded
        self.failed += other.failed
        self.failures += other.failures


def _failure_reason(e):
    """异常 → 用于分组的失败原因文本。"""
    if isinstance(e, ExtractError):
        return e.reason
    return f'其他错误({type(e).__name__}: {e})'


def process_random_dir(rd, output_dir, last_name, label, result):
    """根据 classify_output 的判定结果处理一个随机目录的产物。

    done_a → 直接搬出唯一子目录(沿用其自身名字);
    done_b → 在 output 下用 last_name 建文件夹,把 rd 内文件搬进去;
    need_extract → 对每个未完成项再开新随机目录递归处理。
    label 是到达这一层的压缩链路(如 'a.7z → b.zip'),用于失败记录;
    产物路径与内层失败都记到 result。
    """
    tag, payload = classify_output(rd)

    if tag == 'done_a':
        sole = payload
        target_name = _safe_output_name(output_dir, os.path.basename(sole))
        target = os.path.join(output_dir, target_name)
        shutil.move(sole, target)
        result.produced.append(target)
        return

    if tag == 'done_b':
        target_name = _safe_output_name(output_dir, last_name)
        target = os.path.join(output_dir, target_name)
        os.makedirs(target, exist_ok=True)
        for entry in os.listdir(rd):
            shutil.move(os.path.join(rd, entry),
                        os.path.join(target, entry))
        result.produced.append(target)
        return

    items = payload
    deepest_in_rd = _find_deepest_folder_name(rd)

    work_root = os.path.join(output_dir, 'tmp')
    for item in items:
        item_label = f"{label} → {os.path.basename(item)}"
        new_rd = _new_random_dir(work_root)
        try:
            extract_file(item, new_rd)
        except Exception as e:
            reason = _failure_reason(e)
            print(f"    内层解压失败: {item_label}: {reason}")
            result.add_failure(reason, item_label)
            shutil.rmtree(new_rd, ignore_errors=True)
            continue
        # 命名优先级:该层 archive 自身 stem > rd 中最深目录名 > 父级传下来的 last_name。
        # 这样多层嵌套时最内层 archive 的名字也能被沿用到最终输出。
        item_stem = _archive_stem(os.path.basename(item))
        sub_last_name = item_stem or deepest_in_rd or last_name
        process_random_dir(new_rd, output_dir, sub_last_name, item_label, result)
        shutil.rmtree(new_rd, ignore_errors=True)


def collect_tasks(tmp_dir):
    """列出 tmp_dir 下(含一层子目录)的待解压文件,分卷只保留首卷。"""
    tasks = []
    for name in os.listdir(tmp_dir):
        full = os.path.join(tmp_dir, name)
        if os.path.isfile(full):
            if name != FAIL_LOG_NAME:
                tasks.append(full)
        elif os.path.isdir(full):
            for inner in os.listdir(full):
                inner_full = os.path.join(full, inner)
                if os.path.isfile(inner_full) and inner != FAIL_LOG_NAME:
                    tasks.append(inner_full)
    return _group_split_volumes(tasks)


def extract_tasks(tasks, output_dir):
    """为每个压缩包开一个随机工作目录,解压并递归处理,完成后立刻删工作目录。返回 RunResult。"""
    result = RunResult()
    work_root = os.path.join(output_dir, 'tmp')
    os.makedirs(work_root, exist_ok=True)

    total = len(tasks)
    for i, task in enumerate(tasks, 1):
        name = os.path.basename(task)
        print(f"[{i}/{total}] {name}")
        failures_before = len(result.failures)
        rd = _new_random_dir(work_root)
        try:
            extract_file(task, rd)
            process_random_dir(rd, output_dir, _archive_stem(name), name, result)
        except Exception as e:
            reason = _failure_reason(e)
            print(f"    失败: {reason}")
            result.add_failure(reason, name)
        finally:
            shutil.rmtree(rd, ignore_errors=True)

        if len(result.failures) > failures_before:
            result.failed += 1
        else:
            result.succeeded += 1
            print("    成功")

    try:
        if os.path.isdir(work_root) and not os.listdir(work_root):
            os.rmdir(work_root)
    except OSError:
        pass
    return result


def extract_loop(tmp_dir, output_dir):
    """主流程:解压 tmp_dir 下所有压缩包到 output_dir。返回 RunResult。"""
    return extract_tasks(collect_tasks(tmp_dir), output_dir)


def write_failure_log(log_dir, failures):
    """把失败明细追加写入 log_dir/解压失败日志.txt:每种原因一行,行首带时间戳。"""
    if not failures:
        return None
    grouped = {}
    for reason, name in failures:
        grouped.setdefault(reason, []).append(name)
    stamp = time.strftime('%Y-%m-%d %H:%M:%S')
    log_file = os.path.join(log_dir, FAIL_LOG_NAME)
    with open(log_file, 'a', encoding='utf-8') as f:
        for reason, names in grouped.items():
            f.write(f"{stamp} [{reason}] {' | '.join(names)}\n")
    return log_file


# ============================================================
# 垃圾文件清理
# ============================================================

def purge_garbage(root_dir):
    """递归删除 root_dir 下所有完整名命中 config.json 中 garbage_list 的文件,返回删除数。"""
    names = GARBAGE_NAMES
    removed = 0
    if not names or not os.path.isdir(root_dir):
        return removed
    for cur, _dirs, files in os.walk(root_dir):
        for f in files:
            if f in names:
                target = os.path.join(cur, f)
                try:
                    os.remove(target)
                    removed += 1
                except OSError as e:
                    print(f"删除失败 {target}: {e}")
    return removed


# ============================================================
# 叶子目录文件重命名
# ============================================================

def _extract_digit_runs(stem):
    """提取 stem 中所有连续数字段(按出现顺序,保留前导零)。"""
    return DIGIT_RUN.findall(stem)


def _find_leaf_folders(root):
    """yield root 下所有不含子目录的叶子目录路径。"""
    for cur, dirs, _files in os.walk(root):
        if not dirs:
            yield cur


def _pick_yyy_index(file_runs):
    """选出"整批不撞车"的数字段下标 N。

    挑最小的 N 满足:有至少 N+1 段数字的那些文件,它们的第 N 段彼此唯一。
    第 0 段就互不重复时直接用 0;若批内多个文件第 0 段相同(典型如所有文件
    都以日期 '2023' 开头),整批回退到第 1 段;依此类推。返回 None 表示
    任何 N 都无法消除碰撞 — 这种 leaf 不做改名。
    """
    max_runs = max((len(r) for r in file_runs.values()), default=0)
    for n in range(max_runs):
        values_at_n = {}
        for fname, runs in file_runs.items():
            if len(runs) > n:
                values_at_n.setdefault(runs[n], []).append(fname)
        if values_at_n and all(len(v) == 1 for v in values_at_n.values()):
            return n
    return None


def rename_leaf_files(root_dir):
    """扫所有叶子目录,文件夹名含 No.xxx 的批量重命名为 'xxx - yyy.ext',返回重命名数。"""
    total_renamed = 0
    if not os.path.isdir(root_dir):
        return total_renamed
    for leaf in _find_leaf_folders(root_dir):
        m = NO_PATTERN.search(os.path.basename(leaf))
        if not m:
            continue
        xxx = m.group(1)

        files = [f for f in os.listdir(leaf)
                 if os.path.isfile(os.path.join(leaf, f))]
        if not files:
            continue

        file_runs = {f: _extract_digit_runs(os.path.splitext(f)[0])
                     for f in files}
        n = _pick_yyy_index(file_runs)
        if n is None:
            print(f"重命名跳过(找不到可用的数字编号): {leaf}")
            continue

        for fname in files:
            runs = file_runs[fname]
            if len(runs) <= n:
                continue
            yyy = runs[n]
            ext = os.path.splitext(fname)[1]
            new_name = f"{xxx} - {yyy}{ext}"
            if new_name == fname:
                continue
            src = os.path.join(leaf, fname)
            dst = os.path.join(leaf, new_name)
            if os.path.exists(dst):
                print(f"重命名跳过(目标已存在): {dst}")
                continue
            try:
                os.rename(src, dst)
                total_renamed += 1
            except OSError as e:
                print(f"重命名失败 {fname} -> {new_name}: {e}")
    return total_renamed


# ============================================================
# CLI 入口
# ============================================================

def _open_folder(path):
    """在资源管理器中打开文件夹(仅 Windows;失败静默)。"""
    if hasattr(os, 'startfile'):
        try:
            os.startfile(path)
        except OSError:
            pass


def _print_summary(result, log_files, purged, renamed):
    """打印最终汇总:成功/失败数、按原因分组的失败数、日志位置、清理与重命名数。"""
    print()
    print('=' * 50)
    print(f"完成: 成功 {result.succeeded} 个 / 失败 {result.failed} 个")
    if result.failures:
        counts = {}
        for reason, _name in result.failures:
            counts[reason] = counts.get(reason, 0) + 1
        for reason, n in counts.items():
            print(f"  [{reason}] {n} 个")
        for log_file in log_files:
            print(f"失败日志: {log_file}")
    if purged:
        print(f"清理垃圾文件 {purged} 个")
    if renamed:
        print(f"重命名文件 {renamed} 个")


def run_configured(task):
    """按 config.json 中的 file_path / extract_path 执行指定任务。"""
    result = RunResult()
    log_files = []
    if task in ('extract', 'all'):
        os.makedirs(file_path, exist_ok=True)
        tasks = collect_tasks(file_path)
        if not tasks:
            print(f"待解压文件夹是空的: {file_path}")
            print("把压缩包放进这个文件夹后再运行一次即可。")
            _open_folder(file_path)
            return
        os.makedirs(extract_path, exist_ok=True)
        print(f"待解压: {file_path}")
        print(f"解压到: {extract_path}\n")
        result = extract_tasks(tasks, extract_path)
        log_file = write_failure_log(file_path, result.failures)
        if log_file:
            log_files.append(log_file)
    purged = purge_garbage(extract_path) if task in ('purge', 'all') else 0
    renamed = rename_leaf_files(extract_path) if task in ('rename', 'all') else 0
    _print_summary(result, log_files, purged, renamed)
    if task in ('extract', 'all') and result.produced:
        _open_folder(extract_path)


def run_dropped(paths):
    """拖放模式:文件解压到它所在目录;文件夹解压到旁边的 '<文件夹名>_已解压'。

    清理与重命名只作用于本次新产出的目录,不碰压缩包旁边原有的文件。
    失败日志写在压缩包所在目录(拖入文件夹时写在该文件夹里)。
    """
    jobs = []   # [(日志目录, 任务列表, 输出目录)]
    by_parent = {}
    for p in paths:
        p = os.path.abspath(p)
        if os.path.isfile(p):
            by_parent.setdefault(os.path.dirname(p), []).append(p)
        elif os.path.isdir(p):
            out = os.path.normpath(p) + '_已解压'
            jobs.append((p, collect_tasks(p), out))
    for parent, files in by_parent.items():
        jobs.insert(0, (parent, _group_split_volumes(files), parent))

    total = RunResult()
    log_files = []
    for log_dir, tasks, out in jobs:
        if not tasks:
            print(f"没有可解压的文件: {log_dir}")
            continue
        print(f"解压到: {out}\n")
        result = extract_tasks(tasks, out)
        log_file = write_failure_log(log_dir, result.failures)
        if log_file:
            log_files.append(log_file)
        total.merge(result)

    purged = sum(purge_garbage(p) for p in total.produced)
    renamed = sum(rename_leaf_files(p) for p in total.produced)
    _print_summary(total, log_files, purged, renamed)


def main(argv):
    """解析命令行:参数全是已存在的路径 → 拖放模式;否则按任务名执行。"""
    args = [a for a in argv if a != '--no-pause']
    if args and all(os.path.exists(a) for a in args):
        _apply_config()
        _init_tools()
        run_dropped(args)
        return

    parser = argparse.ArgumentParser(
        description='批量解压 + 清理垃圾文件 + 重命名工具')
    parser.add_argument(
        'task', nargs='?', default='all',
        choices=['extract', 'purge', 'rename', 'all'],
        help='要执行的任务(默认 all: 依次 extract → purge → rename)')
    ns = parser.parse_args(args)
    _apply_config()
    if ns.task in ('extract', 'all'):
        _init_tools()
    run_configured(ns.task)


if __name__ == "__main__":
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
