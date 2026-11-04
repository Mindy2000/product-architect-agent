"""Public failure catalogue: safe causes and actions, never raw OS messages or output."""
import errno
import sqlite3
import subprocess
from . import workbench_archive as archive
from .process_control import OutputLimitExceeded

CATALOG = {
    'resource_busy': ('其他本地操作仍在占用资源。', '暂停开发、关闭预览，等待已有操作结束后手动重试。'),
    'service_interrupted': ('服务重启中断了此维护任务。', '原数据保留，未自动执行；检查任务产物后按需手动重新开始。'),
    'disk_space': ('磁盘空间不足，维护未提交。', '整理磁盘后重新开始；原工作台保留。'),
    'permission_denied': ('本机文件权限不允许此次维护。', '检查安装目录和目标位置的写入权限，不要覆盖原工作台。'),
    'database_busy': ('本地数据库正在被其他操作占用。', '暂停开发、关闭预览及其他工作台进程，稍后手动重试。'),
    'database_invalid': ('本地数据库无法通过读取或完整性检查。', '保留原目录和已有备份；查看数据库健康状态，勿删除原数据库。'),
    'runtime_unavailable': ('所需的本地运行工具或进程不可用。', '在首次使用向导检查 Docker、固定镜像和安装依赖，再手动重试。'),
    'operation_timeout': ('维护超过允许时限，已停止。', '关闭其他预览，检查容器环境后重试；不要提高命令权限。'),
    'output_limit': ('维护输出超过安全上限，已停止。', '检查项目测试是否产生循环或大量日志，缩小检查范围后重试。'),
    'archive_rejected': ('备份或恢复未通过安全检查。', '重新预览大小、兼容版本与文件校验结果；原数据保留。'),
    'source_changed': ('检查期间源文件发生变化，结果未采纳。', '停止编辑后重新开始，使用同一份源码比较。'),
    'unknown_failure': ('本地维护未完成，原数据保留。', '保存任务编号和脱敏诊断；检查安装与磁盘状态后手动重试。'),
}


def describe(error):
    if isinstance(error, archive.ArchiveError):
        code = getattr(error, 'code', 'archive_rejected')
    elif isinstance(error, OSError) and error.errno == errno.ENOSPC: code = 'disk_space'
    elif isinstance(error, PermissionError): code = 'permission_denied'
    elif isinstance(error, FileNotFoundError): code = 'runtime_unavailable'
    elif isinstance(error, sqlite3.OperationalError) and (getattr(error, 'sqlite_errorcode', 0)&255) in {sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED}: code = 'database_busy'
    elif isinstance(error, sqlite3.DatabaseError): code = 'database_invalid'
    elif isinstance(error, subprocess.TimeoutExpired): code = 'operation_timeout'
    elif isinstance(error, OutputLimitExceeded): code = 'output_limit'
    elif isinstance(error, subprocess.SubprocessError): code = 'runtime_unavailable'
    else: code = 'unknown_failure'
    if code not in CATALOG: code = 'unknown_failure'
    message,action = CATALOG[code]
    return {'code':code,'message':message,'next_step':action,'automatic_retry':False}
