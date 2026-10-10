# 开发初衷  
![alt text](image.png)
各大论坛的压缩包文件, 考虑到**安全性**等因素, 其上传版本大多:  
1. ".7z"丢失, 需要用户自己重命名  
2. 多层反复加密, 解压过程繁琐, 且重命名整理过于麻烦  
3. 跨用户密码库需求大, 避免手动输入浪费时间  
4. 水印, 命名水印, 广告文件等处理麻烦影响整合  
5. 解压结果可能以初始文件名命名, 如"学习资料", 有自动识别和取用有效命名的需求  
6. 解压软件变化快, 经典python 7z库不能支持分卷解压, 重命名试错浪费用户时间  

# 用户使用方法
> 不需要安装 Python 或任何解压软件, 下载即用 (仅支持 Windows)

## 下载
1. 打开 [Releases 页面](https://github.com/Momordicin/QuickUnzip/releases), 下载最新的 `QuickUnzip-vX.X.X.zip`  
2. 解压到任意位置 (建议放在 D 盘等普通文件夹, 不要放进 `C:\Program Files`, 否则可能没有写入权限)  
3. 双击 `QuickUnzip.exe` 打开一次, 右键菜单会自动注册好  

解压后的文件夹:
```
QuickUnzip/
├── QuickUnzip.exe     ← 双击打开主窗口
├── config.json        ← 设置 (在程序的 菜单 → 设置 里修改即可)
├── _internal/         ← 程序依赖, 不要动
└── README.md
```
整个文件夹可以随时挪到别的位置, 下次打开程序时右键菜单会自动改成新位置  

## 使用
### 主窗口: 统一解压到一个文件夹
1. 双击 `QuickUnzip.exe`  
2. 把压缩包 / 文件夹拖进窗口, 或点 **选择文件** / **选择文件夹**; 列表里右键或按 `Delete` 可移除单项  
3. 确认底部的 **输出到** 路径, 点 **开始**  
   - 所有任务都解压到这一个文件夹  
   - 输出路径默认值: 这个文件夹以前处理过 → 用它上次的输出路径; 否则用上一次的输出路径; 都没有 → 在第一个文件所在目录下新建 `已解压/`  
   - 处理过的文件夹会出现在 **历史 ▾** 下拉里 (显示最近 3 条, 可点 ✕ 删除)  
4. 进度只占一行, 结束后显示 "成功 N 个 / 失败 M 个", 有失败时点 **查看失败日志**  

### 右键菜单
在资源管理器里右键, 有两项:
- **智能解压到此处**: 压缩包解压到它旁边; 右键文件夹或文件夹空白处时, 把里面的文件解压到这个文件夹里  
- **解压至…**: 打开主窗口并填好选中的文件, 自己选输出路径再开始  

右键菜单出现在压缩包 (`.zip .7z .rar .tar .gz .tgz .bz2 .xz .001`, 含 `.part2.rar` 等分卷)、文件夹、文件夹空白处; 可以一次选中多个 (最多 100 个), 会合并到同一个进度窗口里处理  
改了后缀的压缩包 (如 `资料.666`) 右键不到时, 在它所在文件夹的空白处右键 → 智能解压到此处  

### 拖到图标上
把压缩包或文件夹拖到 `QuickUnzip.exe` (或它的桌面快捷方式) 上, 效果同 **智能解压到此处**  
- 建桌面快捷方式: 按住 `Alt` 把 `QuickUnzip.exe` 拖到桌面  

右键 / 拖图标时会弹出一个小进度窗口: 全部成功自动关闭; 有失败就停住, 可以查看失败日志  

## 设置 (菜单 → 设置)
同一页上下滚动, 点顶部标签跳到对应位置:
- **密码库**: 按从上到下的顺序逐个尝试, 最后再试一次无密码  
- **常用路径**: 处理过的文件夹 → 输出文件夹, 可添加、修改、删除  
- **右键菜单**: 文件 / 文件夹 / 文件夹空白处 分别开关, 也可一键关闭  
- **解压后处理**:  
  - 垃圾清理 (默认开): 删除"垃圾清单"里列出的文件  
  - 重命名 (默认开): 文件夹名含 `No.xxx` 时, 把里面的文件改名为 `xxx - 编号`  
  - 删除源文件 (默认关): 一个压缩包连同里面各层都解压成功后, 把源文件 (分卷则为全部分卷) 移到回收站; 有任何一层失败都保留  
- **垃圾清单**: 写完整文件名 (包含后缀), 如 `广告.txt`  

解压后处理只作用于本次解压出来的文件夹, 输出目录里原有的文件不受影响  

## 解压失败怎么办
有失败时, 会在程序所在文件夹写入 `解压失败日志.txt`, 每种失败原因一行:
```
2026-10-08 14:30:05 [密码错误(密码库中没有匹配的密码)] a.7z | b.zip → inner.7z
2026-10-08 14:30:05 [无法识别的格式或解压失败] c.7z
2026-10-08 14:30:05 [分卷不全(请把所有分卷放在同一文件夹)] d.7z.001(缺少 d.7z.002)
```
- **密码错误**: 在 设置 → 密码库 里加上正确密码后重新解压  
- **分卷不全**: 日志里会写明缺少哪一卷, 补齐后重新解压; 缺最后一卷时文件名上看不出, 会提示"可能缺少最后一卷"  
- **文件不完整**: 下载没完成或文件被截断, 重新下载  
- **压缩包损坏**: 数据校验出错, 重新下载  
- **无法识别的格式**: 该文件可能不是压缩包 (程序会对每个文件都尝试一遍, 不看后缀)  

## 卸载
主窗口 菜单 → 卸载:
1. 先弹出另存为, 把密码本导出成文本文件 (一行一个密码, 默认 `桌面\QuickUnzip密码本.txt`)  
2. 清除右键菜单  
3. 程序关闭后删除 `QuickUnzip.exe`、`_internal/`、`config.json`  

`待解压/`、`已解压/` 及其中的文件、`解压失败日志.txt` 都会保留  

## 常见问题
- **杀毒软件报毒 / Windows 提示"已保护你的电脑"**: 打包程序没有数字签名导致的误报, 点"更多信息 → 仍要运行"; 介意的话可以按下方开发者模式直接运行源码  
- **右键菜单看不到**: 先双击打开一次程序; Windows 11 默认的新版右键菜单需要点"显示更多选项"  
- **拖进窗口没反应**: 不要以管理员身份运行程序, 系统会拦截从普通资源管理器拖进来的文件  

# 开发者模式
## 环境
- Windows, Python >= 3.8, 运行无需任何第三方库 (界面用自带的 tkinter)  
- 7z.exe / 7z.dll / UnRAR.exe 已随仓库提供 (查找顺序: 程序目录 → PATH → Program Files 默认安装路径)  

## 运行源码
```bash
git clone https://github.com/Momordicin/QuickUnzip.git
cd QuickUnzip
python jieya.py            # 打开主窗口; 首次运行自动由 config.example.json 生成 config.json
```
`config.json` 是本地文件, 已加入 `.gitignore`, 不会上传; 修改默认模板请编辑 `config.example.json`  
源码运行时, 右键菜单会注册成 `pythonw.exe jieya.py ...`; 之后运行打包版 exe 会改回指向 exe  

```bash
python jieya.py --here <路径>...                  # 智能解压到此处 (进度小窗口)
python jieya.py --to-dialog <路径>...             # 解压至… (主窗口)
python jieya.py --to <输出文件夹> <路径>...       # 命令行: 统一解压到输出文件夹
python jieya.py --suggest <路径>...               # 命令行: 打印默认输出路径
python -m unittest discover -s tests -t .         # 单元测试
```

## 打包 exe
```bash
pip install -r requirements-dev.txt          # PyInstaller + Pillow(生成多尺寸图标)
python build.py                              # 可选 --version v1.0.0, 默认取 git describe
```
产物:
- `dist/QuickUnzip/`: 可直接运行的程序目录 (onedir 窗口程序; 7z / UnRAR / 配置模板 / 图标打包在 `_internal/` 中)  
- `dist/QuickUnzip-<version>.zip`: 发布用压缩包  

发布包里的 `config.json` 由 `config.example.json` 生成, 不会带上你本地的 `config.json`  

## 发布新版本
推送 `v` 开头的标签, GitHub Actions (`.github/workflows/release.yml`) 会自动打包并发布到 Releases:
```bash
git tag v1.1.0
git push origin v1.1.0
```

## 项目结构
```
jieya.py                     入口: 按启动参数打开主窗口 / 进度小窗口 / 命令行
quickunzip/
├── paths.py                 程序路径的唯一来源 (APP_DIR 等)
├── config.py                config.json 读写、旧配置转换、历史映射与默认输出路径
├── core.py                  解压引擎 (无界面)
├── gui_main.py              主窗口
├── gui_settings.py          设置窗口
├── gui_progress.py          右键 / 拖图标时的进度小窗口
├── dnd.py                   拖入窗口 (ctypes)
├── shell_menu.py            右键菜单注册表
├── single_instance.py       多选时合并成一个实例
└── uninstall.py             卸载
tests/                       单元测试
build.py                     打包脚本
assets/                      程序图标
config.example.json          配置模板 (首次运行 / 打包时生成 config.json)
7z.exe 7z.dll UnRAR.exe      外部解压工具 (Windows)
7zz 7zzs                     7-Zip Linux 版本 (当前未使用)
```

# 致谢
感谢[toolUnRar](https://github.com/Mario-Hero/toolUnRar)项目的启发, 免去了python库依赖, 提供了分卷解压问题的解决方案 

This software uses 7-Zip (https://www.7-zip.org/)  
7-Zip is licensed under the GNU LGPL license.  

This software uses UnRAR (https://www.rarlab.com/rar_add.htm)
UnRAR is freeware with source code, developed by RARLAB (Alexander Roshal).
UnRAR may be used freely, but it may NOT be used to develop a RAR 
(de)compression library or to re-create the RAR compression algorithm.
