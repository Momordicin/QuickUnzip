"""解压引擎(不含界面)。

把压缩包(zip / 7z / rar / .001 分卷 / .partN.rar,以及任何改了后缀的压缩包)递归解压,
过程中自动判定何时停止递归,并把产物按"压缩链路最后一层有意义的名字"放进输出目录。
完成后可清理已知垃圾文件、按文件夹的 No.xxx 编号批量重命名叶子目录里的文件、
把解压成功的源文件移到回收站。

两种任务规划:
    plan_unified(paths, output_dir)   全部输入解压到同一个输出文件夹(主窗口)
    plan_in_place(paths)              文件解压到它旁边,文件夹解压到它里面(右键 / 拖到图标)
规划结果交给 Extractor.run() 执行;进度、停止、日志都通过参数注入,引擎本身不打印。
"""

import os
import re
import shutil
import secrets
import subprocess
import time

from . import paths
from .config import UserError


# ============================================================
# 常量
# ============================================================

SPLIT_VOL_RE = re.compile(r'\.(\d{3})$')                      # .001 / .002 ...
PART_RAR_RE = re.compile(r'\.part(\d+)\.rar$', re.IGNORECASE)  # .part1.rar / .part2.rar ...
NO_PATTERN = re.compile(r'[Nn][Oo]\.(\d+)')                   # 文件夹名中的 No.xxx
DIGIT_RUN = re.compile(r'\d+')                                # 任一段连续数字

# 解压中间产物的工作目录,建在输出目录下,用完即删
WORK_DIR_NAME = '.quickunzip_tmp'

# 后缀白名单:落在这个集合内的文件视为"已是终态产物,无须再解压";集合外
# (无后缀 / '.7z删除' / '.tar' / ...)一律视为"还要处理",交给 7z.exe 按文件头
# sniff 决定下一步。只作用于嵌套内层;顶层任务不看后缀,全部尝试。
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

REASON_BAD_PASSWORD = '密码错误(密码库中没有匹配的密码)'
REASON_MISSING_VOLUME = '分卷不全(请把所有分卷放在同一文件夹)'
REASON_TRUNCATED = '文件不完整(下载未完成或被截断)'
REASON_CORRUPT = '压缩包损坏(CRC 或数据错误)'
REASON_UNSUPPORTED = '无法识别的格式或解压失败'


def _noop(*_args, **_kwargs):
    pass


# ============================================================
# 外部工具 (7z.exe + UnRAR.exe)
# ============================================================

def _find_tool(exe_name, which_names, fallback):
    """按 TOOL_DIRS → PATH → 默认安装路径 顺序定位外部工具,找不到抛 FileNotFoundError。"""
    for d in paths.TOOL_DIRS:
        local = os.path.join(d, exe_name)
        if os.path.isfile(local):
            return local
    for name in which_names:
        found = shutil.which(name)
        if found:
            return found
    if os.path.isfile(fallback):
        return fallback
    raise FileNotFoundError(f"{exe_name} not found in {paths.APP_DIR}, PATH, or {fallback}")


SEVEN_ZIP_EXE = None
UNRAR_EXE = None


def init_tools():
    """定位 7z.exe / UnRAR.exe;找不到时转成面向用户的错误。"""
    global SEVEN_ZIP_EXE, UNRAR_EXE
    try:
        SEVEN_ZIP_EXE = _find_tool('7z.exe', ['7z'], r'C:\Program Files\7-Zip\7z.exe')
        UNRAR_EXE = _find_tool('UnRAR.exe', ['UnRAR', 'unrar'],
                               r'C:\Program Files\WinRAR\UnRAR.exe')
    except FileNotFoundError as e:
        raise UserError(
            f"找不到解压工具: {e}\n"
            "源码运行: 请确认 7z.exe / 7z.dll / UnRAR.exe 在仓库根目录;\n"
            "exe 版本: 请重新完整解压发布包, 不要单独拷走 QuickUnzip.exe。")


# 窗口程序里调用控制台工具时不弹黑框
_NO_WINDOW = getattr(subprocess, 'CREATE_NO_WINDOW', 0)


def _run_tool(cmd):
    return subprocess.run(
        cmd, capture_output=True, text=True,
        encoding='utf-8', errors='replace',
        stdin=subprocess.DEVNULL, creationflags=_NO_WINDOW,
    )


def extract_with_7z(file_path, output_path, password):
    """调 7z.exe 解压。返回 'ok' | 'bad_password' | 'incomplete' | 'corrupt' | 'fail'。"""
    pw_arg = f'-p{password}' if password else '-p'
    result = _run_tool([SEVEN_ZIP_EXE, 'x', file_path, f'-o{output_path}',
                        '-aoa', '-y', pw_arg])
    if result.returncode == 0:
        return 'ok'
    blob = (result.stderr or '') + (result.stdout or '')
    if 'Wrong password' in blob or 'Can not open encrypted archive' in blob:
        return 'bad_password'
    # 缺分卷 / 文件被截断时 7z 报 'Unexpected end of archive',与密码无关;
    # 分卷 zip 缺卷时还会同时报 CRC Failed,所以必须先于 CRC 判断。
    if 'Unexpected end of archive' in blob:
        return 'incomplete'
    if 'CRC' in blob or 'Data error' in blob:
        return 'corrupt'
    return 'fail'


def extract_with_unrar(file_path, output_path, password):
    """调 UnRAR.exe 解压 .rar / .partN.rar。返回 'ok' | 'bad_password' | 'incomplete' | 'corrupt' | 'fail'。"""
    pw_arg = f'-p{password}' if password else '-p-'
    out = output_path
    # UnRAR 要求输出路径必须以分隔符结尾,否则它会把路径当成单个目标文件名而非目录。
    if not out.endswith(os.sep) and not out.endswith('/'):
        out = out + os.sep
    result = _run_tool([UNRAR_EXE, 'x', '-o+', '-y', pw_arg, file_path, out])
    if result.returncode == 0:
        return 'ok'
    # 缺卷时 UnRAR 的退出码与密码错误/无文件可解有重叠,先按输出文本识别。
    blob = (result.stderr or '') + (result.stdout or '')
    if any(s in blob for s in ('Cannot find volume',
                               'start extraction from a previous volume',
                               'Unexpected end of archive')):
        return 'incomplete'
    if result.returncode in (10, 11):
        return 'bad_password'
    if result.returncode == 3:
        return 'corrupt'
    return 'fail'


def _is_rar_file(file_path):
    """判定是否 rar 系列文件(.rar / .partN.rar),用于分派到 UnRAR.exe。"""
    lower = file_path.lower()
    return lower.endswith('.rar') or bool(PART_RAR_RE.search(lower))


# ============================================================
# 单个压缩包
# ============================================================

class ExtractError(Exception):
    """解压失败;reason 是用于汇总分组的失败原因(上面的 REASON_* 之一),
    detail 是附在文件名后的补充说明(如缺了哪几卷),可为 None。"""

    def __init__(self, reason, path, detail=None):
        super().__init__(f"{reason}: {path}")
        self.reason = reason
        self.detail = detail


def _volume_key(path):
    """分卷文件 → ((去掉卷号的路径, 类型), 卷号, 卷号位数);非分卷返回 None。"""
    m = PART_RAR_RE.search(path)
    if m:
        return (path[:m.start()].lower(), 'part'), int(m.group(1)), len(m.group(1))
    m = SPLIT_VOL_RE.search(path)
    if m:
        return (path[:m.start()].lower(), 'num'), int(m.group(1)), len(m.group(1))
    return None


def _sibling_volumes(path):
    """同目录下与 path 同组的所有分卷 {卷号: 完整路径};非分卷返回 {}。"""
    vk = _volume_key(path)
    if vk is None:
        return {}
    key = vk[0]
    folder = os.path.dirname(path) or '.'
    found = {}
    for name in os.listdir(folder):
        full = os.path.join(folder, name)
        other = _volume_key(full)
        if other and other[0] == key and os.path.isfile(full):
            found[other[1]] = full
    return found


def _missing_volumes(path):
    """按同目录下的文件名找出缺失的分卷(第 1 卷到现有最大卷号之间的空缺)。

    返回 (缺失卷名列表, 同组现有卷数)。只能发现"中间"或"第一卷"缺失;
    最后一卷缺失从文件名上看不出,要靠解压工具报错('incomplete')兜底。
    """
    vk = _volume_key(path)
    if vk is None:
        return [], 0
    key, _num, width = vk
    present = set(_sibling_volumes(path))

    stem = _archive_stem(os.path.basename(path))
    if key[1] == 'part':
        def vol_name(n):
            return f"{stem}.part{n:0{width}d}.rar"
    else:
        def vol_name(n):
            return f"{stem}.{n:0{width}d}"
    return [vol_name(n) for n in range(1, max(present) + 1) if n not in present], len(present)


def source_files(path):
    """一个顶层任务对应的全部源文件:分卷返回同组所有卷,否则只有它自己。"""
    vols = _sibling_volumes(path)
    return [vols[n] for n in sorted(vols)] if vols else [path]


def extract_file(file_path, output_path, passwords):
    """解压单个压缩包到 output_path:按后缀分派工具、轮询密码,全失败则抛 ExtractError。"""
    missing, n_present = _missing_volumes(file_path)
    missing_detail = f"缺少 {', '.join(missing)}" if missing else None
    # 同组只有这一个文件(如 '资料.666')时,它也可能只是被改了后缀的单个压缩包,
    # 先照常尝试;多卷且有空缺时才直接判定缺卷。
    if missing and n_present > 1:
        raise ExtractError(REASON_MISSING_VOLUME, file_path, missing_detail)

    os.makedirs(output_path, exist_ok=True)
    extractor = extract_with_unrar if _is_rar_file(file_path) else extract_with_7z

    # 末尾追加 None = "最后一轮不带密码再试一次",兜住完全无密码的包。
    saw_bad_password = False
    for pw in list(passwords) + [None]:
        status = extractor(file_path, output_path, pw)
        if status == 'ok':
            return
        # 缺卷/截断与密码无关,换密码也没用,立即停止。
        if status == 'incomplete':
            if _volume_key(file_path):
                raise ExtractError(REASON_MISSING_VOLUME, file_path,
                                   missing_detail or "可能缺少最后一卷, 或某一卷没下载完整")
            raise ExtractError(REASON_TRUNCATED, file_path)
        if status == 'corrupt':
            raise ExtractError(REASON_CORRUPT, file_path)
        if status == 'bad_password':
            saw_bad_password = True
    if missing:
        raise ExtractError(REASON_MISSING_VOLUME, file_path, missing_detail)
    # 从没报过密码错误 → 大概率根本不是压缩包或格式不支持,而不是密码问题。
    reason = REASON_BAD_PASSWORD if saw_bad_password else REASON_UNSUPPORTED
    raise ExtractError(reason, file_path)


# ============================================================
# 文件名与目录工具
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
    """把 .001 / .002 ... 与 .part1.rar / .part2.rar ... 分卷归组,每组只保留卷号最小的一卷
    (其余由 7z / UnRAR 自动联动)。最小卷号不是 1 时照样保留,由 extract_file 报"缺少第一卷"。"""
    grouped = {}
    singles = []
    for fp in file_paths:
        vk = _volume_key(fp)
        if vk:
            key, num, _width = vk
            grouped.setdefault(key, []).append((num, fp))
        else:
            singles.append(fp)
    result = list(singles)
    for _base, vols in grouped.items():
        vols.sort()
        result.append(vols[0][1])
    return result


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


# ============================================================
# 智能判定
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


# ============================================================
# 任务规划
# ============================================================

def _path_key(p):
    return os.path.normcase(os.path.normpath(os.path.abspath(p)))


def collect_tasks(folder, exclude=()):
    """列出 folder 下(含一层子目录)的所有文件作为顶层任务,分卷只保留首卷。

    不看后缀:每个文件都交给 extract_file 按文件头识别。exclude 中的子目录
    (例如输出目录就在输入文件夹里时)以及工作目录、失败日志跳过。
    """
    skip = {_path_key(p) for p in exclude}
    tasks = []
    for name in os.listdir(folder):
        full = os.path.join(folder, name)
        if os.path.isfile(full):
            if name != paths.FAIL_LOG_NAME:
                tasks.append(full)
        elif os.path.isdir(full):
            if name == WORK_DIR_NAME or _path_key(full) in skip:
                continue
            for inner in os.listdir(full):
                inner_full = os.path.join(full, inner)
                if os.path.isfile(inner_full) and inner != paths.FAIL_LOG_NAME:
                    tasks.append(inner_full)
    return _group_split_volumes(tasks)


def _dedupe_jobs(jobs):
    seen = set()
    out = []
    for task, output_dir in jobs:
        k = _path_key(task)
        if k not in seen:
            seen.add(k)
            out.append((task, output_dir))
    return out


def plan_unified(inputs, output_dir):
    """主窗口: 所有输入(文件 / 文件夹,可分散在不同位置)解压到同一个 output_dir。"""
    output_dir = os.path.abspath(output_dir)
    tasks = []
    for p in inputs:
        p = os.path.abspath(p)
        if os.path.isfile(p):
            tasks.append(p)
        elif os.path.isdir(p):
            tasks += collect_tasks(p, exclude=[output_dir])
    tasks = _group_split_volumes(tasks)
    return _dedupe_jobs([(t, output_dir) for t in tasks])


def plan_in_place(inputs):
    """右键"智能解压到此处" / 拖到图标: 文件解压到它所在的文件夹,
    文件夹把它下面的任务解压到它自己里面。"""
    files, jobs = [], []
    for p in inputs:
        p = os.path.abspath(p)
        if os.path.isfile(p):
            files.append(p)
        elif os.path.isdir(p):
            jobs += [(t, p) for t in collect_tasks(p)]
    jobs = [(t, os.path.dirname(t)) for t in _group_split_volumes(files)] + jobs
    return _dedupe_jobs(jobs)


# ============================================================
# 结果与失败日志
# ============================================================

class RunResult:
    """一次批量解压的结果。"""

    def __init__(self):
        self.total = 0          # 规划的顶层任务数
        self.produced = []      # 搬进输出目录的顶层产物路径
        self.succeeded = 0      # 顶层任务数(含内层全部成功)
        self.failed = 0         # 顶层任务数(本层或任一内层失败)
        self.failures = []      # [(reason, 显示名)]
        self.stopped = False    # 是否被用户停止
        self.skipped = 0        # 停止时尚未处理的顶层任务数
        self.deleted = 0        # 移到回收站的源文件数
        self.purged = 0         # 清理的垃圾文件数
        self.renamed = 0        # 重命名的文件数
        self.log_file = None    # 本次写入的失败日志,无失败时为 None

    def add_failure(self, reason, name):
        self.failures.append((reason, name))


def _describe_failure(e, name):
    """异常 → (用于分组的失败原因, 带补充说明的显示名)。"""
    if isinstance(e, ExtractError):
        if e.detail:
            name = f"{name}({e.detail})"
        return e.reason, name
    return f'其他错误({type(e).__name__}: {e})', name


def write_failure_log(failures, log_file=None):
    """把失败明细追加写入失败日志:每种原因一行,行首带时间戳。返回日志路径。"""
    if not failures:
        return None
    log_file = log_file or paths.FAIL_LOG_FILE
    grouped = {}
    for reason, name in failures:
        grouped.setdefault(reason, []).append(name)
    stamp = time.strftime('%Y-%m-%d %H:%M:%S')
    with open(log_file, 'a', encoding='utf-8') as f:
        for reason, names in grouped.items():
            f.write(f"{stamp} [{reason}] {' | '.join(names)}\n")
    return log_file


# ============================================================
# 回收站
# ============================================================

def send_to_trash(file_paths):
    """把文件移到回收站(SHFileOperationW + FOF_ALLOWUNDO)。成功返回 True。"""
    if os.name != 'nt' or not file_paths:
        return False
    import ctypes
    from ctypes import wintypes

    class SHFILEOPSTRUCTW(ctypes.Structure):
        _fields_ = [
            ('hwnd', wintypes.HWND),
            ('wFunc', wintypes.UINT),
            ('pFrom', wintypes.LPCWSTR),
            ('pTo', wintypes.LPCWSTR),
            ('fFlags', ctypes.c_uint16),
            ('fAnyOperationsAborted', wintypes.BOOL),
            ('hNameMappings', ctypes.c_void_p),
            ('lpszProgressTitle', wintypes.LPCWSTR),
        ]

    FO_DELETE = 0x0003
    FOF_SILENT = 0x0004
    FOF_NOCONFIRMATION = 0x0010
    FOF_ALLOWUNDO = 0x0040
    FOF_NOERRORUI = 0x0400

    # pFrom 是以 \0 分隔、以 \0\0 结尾的绝对路径列表
    joined = '\0'.join(os.path.abspath(p) for p in file_paths) + '\0\0'
    buf = ctypes.create_unicode_buffer(joined, len(joined))
    op = SHFILEOPSTRUCTW()
    op.wFunc = FO_DELETE
    op.pFrom = ctypes.cast(buf, wintypes.LPCWSTR)
    op.fFlags = FOF_ALLOWUNDO | FOF_NOCONFIRMATION | FOF_SILENT | FOF_NOERRORUI
    rc = ctypes.windll.shell32.SHFileOperationW(ctypes.byref(op))
    return rc == 0 and not op.fAnyOperationsAborted


# ============================================================
# 解压后处理: 垃圾清理 / 叶子目录重命名
# ============================================================

def purge_garbage(root_dir, names, log=_noop):
    """递归删除 root_dir 下所有完整文件名命中 names 的文件,返回删除数。"""
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
                    log(f"删除失败 {target}: {e}")
    return removed


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


def rename_leaf_files(root_dir, log=_noop):
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
            log(f"重命名跳过(找不到可用的数字编号): {leaf}")
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
                log(f"重命名跳过(目标已存在): {dst}")
                continue
            try:
                os.rename(src, dst)
                total_renamed += 1
            except OSError as e:
                log(f"重命名失败 {fname} -> {new_name}: {e}")
    return total_renamed


# ============================================================
# 执行
# ============================================================

class Extractor:
    """执行规划好的任务列表 [(源文件, 输出目录)]。

    on_progress(done, total, current_name, result):
        每个顶层任务开始前调用一次(current_name 为该任务名),全部结束后再调用一次
        (current_name 为 None)。done 是已处理完的顶层任务数。
    stop_event: threading.Event 之类带 is_set() 的对象;置位后处理完当前任务即停止。
    log(msg): 次要信息(重命名跳过、删除失败等)。
    trash(file_paths) -> bool: 删除源文件的实现,默认移到回收站。
    """

    def __init__(self, passwords, garbage_names=(), purge=True, rename=True,
                 delete_source=False, on_progress=None, stop_event=None,
                 log=None, trash=send_to_trash, fail_log=None):
        self.passwords = list(passwords)
        self.garbage_names = set(garbage_names)
        self.purge = purge
        self.rename = rename
        self.delete_source = delete_source
        self.on_progress = on_progress or _noop
        self.stop_event = stop_event
        self.log = log or _noop
        self.trash = trash
        self.fail_log = fail_log

    @classmethod
    def from_config(cls, cfg, **kwargs):
        pp = cfg.post_process
        return cls(cfg.passwords, cfg.garbage_names,
                   purge=pp['purge_garbage'], rename=pp['rename'],
                   delete_source=pp['delete_source'], **kwargs)

    def _stopping(self):
        return bool(self.stop_event and self.stop_event.is_set())

    def run(self, jobs):
        if SEVEN_ZIP_EXE is None:
            init_tools()
        result = RunResult()
        result.total = len(jobs)
        done = 0
        for task, output_dir in jobs:
            if self._stopping():
                result.stopped = True
                result.skipped = result.total - done
                break
            self.on_progress(done, result.total, os.path.basename(task), result)
            self._run_one(task, output_dir, result)
            done += 1

        for p in result.produced:
            if self.purge:
                result.purged += purge_garbage(p, self.garbage_names, self.log)
            if self.rename:
                result.renamed += rename_leaf_files(p, self.log)
        result.log_file = write_failure_log(result.failures, self.fail_log)
        self.on_progress(done, result.total, None, result)
        return result

    def _run_one(self, task, output_dir, result):
        """解压一个顶层任务并递归处理产物;全部成功且开启了删除源文件时移到回收站。"""
        name = os.path.basename(task)
        failures_before = len(result.failures)
        work_root = os.path.join(output_dir, WORK_DIR_NAME)
        rd = _new_random_dir(work_root)
        try:
            extract_file(task, rd, self.passwords)
            self._process_random_dir(rd, output_dir, work_root,
                                     _archive_stem(name), name, result)
        except Exception as e:
            reason, shown = _describe_failure(e, name)
            result.add_failure(reason, shown)
        finally:
            shutil.rmtree(rd, ignore_errors=True)
            try:
                os.rmdir(work_root)     # 只在空时成功
            except OSError:
                pass

        if len(result.failures) > failures_before:
            result.failed += 1
            return
        result.succeeded += 1
        if self.delete_source:
            files = source_files(task)
            if self.trash(files):
                result.deleted += len(files)
            else:
                self.log(f"源文件移到回收站失败: {name}")

    def _process_random_dir(self, rd, output_dir, work_root, last_name, label, result):
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
            target = os.path.join(output_dir,
                                  _safe_output_name(output_dir, os.path.basename(sole)))
            shutil.move(sole, target)
            result.produced.append(target)
            return

        if tag == 'done_b':
            target = os.path.join(output_dir, _safe_output_name(output_dir, last_name))
            os.makedirs(target, exist_ok=True)
            for entry in os.listdir(rd):
                shutil.move(os.path.join(rd, entry), os.path.join(target, entry))
            result.produced.append(target)
            return

        deepest_in_rd = _find_deepest_folder_name(rd)
        for item in payload:
            item_label = f"{label} → {os.path.basename(item)}"
            new_rd = _new_random_dir(work_root)
            try:
                extract_file(item, new_rd, self.passwords)
            except Exception as e:
                reason, shown = _describe_failure(e, item_label)
                result.add_failure(reason, shown)
                shutil.rmtree(new_rd, ignore_errors=True)
                continue
            # 命名优先级:该层 archive 自身 stem > rd 中最深目录名 > 父级传下来的 last_name。
            # 这样多层嵌套时最内层 archive 的名字也能被沿用到最终输出。
            item_stem = _archive_stem(os.path.basename(item))
            sub_last_name = item_stem or deepest_in_rd or last_name
            self._process_random_dir(new_rd, output_dir, work_root,
                                     sub_last_name, item_label, result)
            shutil.rmtree(new_rd, ignore_errors=True)
