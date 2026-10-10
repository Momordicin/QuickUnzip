"""QuickUnzip: 批量递归解压 + 垃圾清理 + 重命名。

模块划分:
    paths   程序所在路径(APP_DIR)及由它推导的所有程序相关路径
    config  config.json 读写、旧配置转换、历史映射与默认输出路径规则
    core    解压引擎(不含任何界面)
"""
