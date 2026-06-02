"""批量解压 + 清理 + 重命名工具。

把 file_path 下的压缩包(zip / 7z / rar / .001 分卷 / .partN.rar)递归解压到
extract_path,过程中自动判定何时停止递归,并把产物按"压缩链路最后一层有意义
的名字"放进 output。完成后可清理已知垃圾文件、按文件夹的 No.xxx 编号批量重命
名叶子目录里的文件。

CLI:
    python jieya.py [extract|purge|rename|all]   (不带参时默认 all)

依赖外部 7z.exe / UnRAR.exe(优先脚本同目录,其次 PATH,最后 Program Files 默认安装路径)。
"""

import argparse
import os
import re
import shutil
import secrets
import subprocess


# ============================================================
# 配置与常量
# ============================================================

# 路径配置
file_path = "E:/downloads/test1/tmp/"
extract_path = "E:/downloads/test1/extracted/"

# 密码列表(extract_file 会按顺序尝试,末尾再追加 None 表示"无密码兜底")
PASSWORDS = ['anon', '1234']

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

# 脚本目录与外部资源路径(便携式 — 工具与垃圾清单都跟着 jieya.py 走)
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
GARBAGE_LIST_FILE = os.path.join(SCRIPT_DIR, 'garbage_list.txt')


# ============================================================
# 外部工具调用 (7z.exe + UnRAR.exe)
# ============================================================

def _find_7z_exe():
    """按 SCRIPT_DIR → PATH → 默认安装路径 顺序定位 7z.exe,找不到抛错。"""
    local = os.path.join(SCRIPT_DIR, '7z.exe')
    if os.path.isfile(local):
        return local
    found = shutil.which('7z')
    if found:
        return found
    fallback = r'C:\Program Files\7-Zip\7z.exe'
    if os.path.isfile(fallback):
        return fallback
    raise FileNotFoundError(
        f"7z.exe not found in {SCRIPT_DIR}, PATH, "
        "or C:\\Program Files\\7-Zip\\7z.exe"
    )


def _find_unrar_exe():
    """按 SCRIPT_DIR → PATH → 默认安装路径 顺序定位 UnRAR.exe,找不到抛错。"""
    for name in ('UnRAR.exe', 'unrar.exe'):
        local = os.path.join(SCRIPT_DIR, name)
        if os.path.isfile(local):
            return local
    found = shutil.which('UnRAR') or shutil.which('unrar')
    if found:
        return found
    fallback = r'C:\Program Files\WinRAR\UnRAR.exe'
    if os.path.isfile(fallback):
        return fallback
    raise FileNotFoundError(
        f"UnRAR.exe not found in {SCRIPT_DIR}, PATH, "
        "or C:\\Program Files\\WinRAR\\UnRAR.exe"
    )


SEVEN_ZIP_EXE = _find_7z_exe()
UNRAR_EXE = _find_unrar_exe()


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


def extract_file(file_path, output_path):
    """解压单个压缩包到 output_path:按后缀分派工具、轮询密码,全失败则抛错。"""
    os.makedirs(output_path, exist_ok=True)
    extractor = extract_with_unrar if _is_rar_file(file_path) else extract_with_7z

    # 末尾追加 None = "最后一轮不带密码再试一次",兜住完全无密码的包。
    for pw in PASSWORDS + [None]:
        status = extractor(file_path, output_path, pw)
        if status == 'ok':
            return
        if status == 'corrupt':
            print(f"Corrupt archive: {file_path}")
            raise RuntimeError(f"Corrupt archive: {file_path}")
    raise RuntimeError(f"All passwords failed for: {file_path}")


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


def process_random_dir(rd, output_dir, last_name):
    """根据 classify_output 的判定结果处理一个随机目录的产物。

    done_a → 直接搬出唯一子目录(沿用其自身名字);
    done_b → 在 output 下用 last_name 建文件夹,把 rd 内文件搬进去;
    need_extract → 对每个未完成项再开新随机目录递归处理。
    """
    tag, payload = classify_output(rd)

    if tag == 'done_a':
        sole = payload
        target_name = _safe_output_name(output_dir, os.path.basename(sole))
        shutil.move(sole, os.path.join(output_dir, target_name))
        return

    if tag == 'done_b':
        target_name = _safe_output_name(output_dir, last_name)
        target = os.path.join(output_dir, target_name)
        os.makedirs(target, exist_ok=True)
        for entry in os.listdir(rd):
            shutil.move(os.path.join(rd, entry),
                        os.path.join(target, entry))
        return

    items = payload
    deepest_in_rd = _find_deepest_folder_name(rd)

    work_root = os.path.join(output_dir, 'tmp')
    for item in items:
        new_rd = _new_random_dir(work_root)
        try:
            extract_file(item, new_rd)
        except Exception as e:
            print(f"Skip (extract failed): {item}: {e}")
            shutil.rmtree(new_rd, ignore_errors=True)
            continue
        # 命名优先级:该层 archive 自身 stem > rd 中最深目录名 > 父级传下来的 last_name。
        # 这样多层嵌套时最内层 archive 的名字也能被沿用到最终输出。
        item_stem = _archive_stem(os.path.basename(item))
        sub_last_name = item_stem or deepest_in_rd or last_name
        process_random_dir(new_rd, output_dir, sub_last_name)
        shutil.rmtree(new_rd, ignore_errors=True)


def extract_loop(tmp_dir, output_dir):
    """主流程:为 tmp_dir 下每个压缩包开一个随机工作目录,解压并递归处理,完成后立刻删工作目录。"""
    work_root = os.path.join(output_dir, 'tmp')
    os.makedirs(work_root, exist_ok=True)

    tasks = []
    for name in os.listdir(tmp_dir):
        full = os.path.join(tmp_dir, name)
        if os.path.isfile(full):
            tasks.append(full)
        elif os.path.isdir(full):
            for inner in os.listdir(full):
                inner_full = os.path.join(full, inner)
                if os.path.isfile(inner_full):
                    tasks.append(inner_full)

    tasks = _group_split_volumes(tasks)

    for task in tasks:
        stem = _archive_stem(os.path.basename(task))

        rd = _new_random_dir(work_root)
        try:
            extract_file(task, rd)
        except Exception as e:
            print(f"Skip (extract_loop failed): {task}: {e}")
            shutil.rmtree(rd, ignore_errors=True)
            continue

        process_random_dir(rd, output_dir, stem)
        shutil.rmtree(rd, ignore_errors=True)

    try:
        if os.path.isdir(work_root) and not os.listdir(work_root):
            os.rmdir(work_root)
    except OSError:
        pass


# ============================================================
# 垃圾文件清理
# ============================================================

def _load_garbage_list():
    """读 garbage_list.txt,返回要删除的完整文件名集合(忽略空行和 # 注释行)。"""
    if not os.path.isfile(GARBAGE_LIST_FILE):
        return set()
    names = set()
    with open(GARBAGE_LIST_FILE, encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            names.add(line)
    return names


def purge_garbage(root_dir):
    """递归删除 root_dir 下所有完整名命中 garbage_list.txt 的文件。"""
    names = _load_garbage_list()
    if not names or not os.path.isdir(root_dir):
        return
    removed = 0
    for cur, _dirs, files in os.walk(root_dir):
        for f in files:
            if f in names:
                target = os.path.join(cur, f)
                try:
                    os.remove(target)
                    removed += 1
                except OSError as e:
                    print(f"Failed to remove {target}: {e}")
    print(f"Purged {removed} garbage file(s) from {root_dir}")


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
    """扫所有叶子目录,文件夹名含 No.xxx 的批量重命名为 'xxx - yyy.ext'。"""
    if not os.path.isdir(root_dir):
        return
    total_renamed = 0
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
            print(f"Rename: no usable digit segment in {leaf}")
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
                print(f"Rename skip (target exists): {dst}")
                continue
            try:
                os.rename(src, dst)
                total_renamed += 1
            except OSError as e:
                print(f"Rename failed {fname} -> {new_name}: {e}")
    print(f"Renamed {total_renamed} file(s) under {root_dir}")


# ============================================================
# CLI 入口
# ============================================================

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description='Batch archive extraction + cleanup + rename utility')
    parser.add_argument(
        'task', nargs='?', default='all',
        choices=['extract', 'purge', 'rename', 'all'],
        help='Which task to run (default: all → extract, then purge, then rename)')
    args = parser.parse_args()

    try:
        if args.task in ('extract', 'all'):
            if not os.path.exists(file_path):
                os.makedirs(file_path)
            extract_loop(file_path, extract_path)
        if args.task in ('purge', 'all'):
            purge_garbage(extract_path)
        if args.task in ('rename', 'all'):
            rename_leaf_files(extract_path)
    except Exception as e:
        print(e)
