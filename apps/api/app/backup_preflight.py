"""Capacity explanation separate from archive creation and restore operations."""
import shutil


def summarize(root, files, *, max_bytes, max_file, max_entries, excluded):
    groups = {}
    oversized = 0
    for name, (size, _) in files.items():
        group = '/'.join(name.split('/')[:2]) if name.startswith('data/') else name.split('/')[0]
        value = groups.setdefault(group, {'files': 0, 'bytes': 0, 'oversized_files': 0})
        value['files'] += 1
        value['bytes'] += size
        value['oversized_files'] += int(size > max_file)
        oversized += int(size > max_file)
    total = sum(value[0] for value in files.values())
    free = shutil.disk_usage(root).free
    required = total * 2 + 128 * 1024**2
    reasons = []
    if oversized:
        reasons.append({'code': 'file_size', 'message': f'{oversized} 个文件超过单文件上限。请查看对应类别，原文件未修改。'})
    if len(files) > max_entries:
        reasons.append({'code': 'file_count', 'message': f'文件数超过 {max_entries:,} 上限。'})
    if total > max_bytes - 8 * 1024**2:
        reasons.append({'code': 'total_size', 'message': '资料大小超过可用备份容量，需为清单和快照保留 8 MiB。'})
    if free < required:
        reasons.append({'code': 'disk_space', 'message': '可用磁盘不足以同时保留原资料和创建备份。'})
    return {'file_count': len(files), 'bytes': total,
            'groups': [{'name': name, **value} for name, value in sorted(groups.items(), key=lambda item: -item[1]['bytes'])],
            'excluded': excluded, 'credentials_included': False, 'format': 1,
            'max_bytes': max_bytes, 'max_file_bytes': max_file, 'max_entries': max_entries,
            'available_disk_bytes': free, 'required_disk_bytes_estimate': required,
            'can_export': not reasons, 'blockers': reasons,
            'estimate_notice': '这是当前文件大小估算，未扫描正文或校验数据库；SQLite 日志合并和并发修改可能改变快照大小，实际导出时再次检查。',
            'recovery_effects': ['恢复到独立目录，保留当前工作台', '保留项目、费用和检查点记录', '密钥不进入备份；恢复后调用关闭', '排除的缓存和运行环境需要重新准备'],
            'warning': '备份包含项目、文档正文和业务资料，请私下保管；不会上传到任何平台。用户资料正文原样保留。'}


def restore_effects(manifest):
    migration = manifest['compatibility']
    return {'original_preserved': True, 'automatic_execution': False,
            'credentials_restored': False, 'calls_enabled': False,
            'database_integrity': '恢复副本中逐库检查；不启动生成代码。',
            'rollback': '停止新副本后可回到原目录；新副本中的后续数据不会自动合并回旧目录。',
            'migration': '保留迁移前原始备份后执行已登记迁移。' if migration['migration_required'] else '同版本恢复，不执行跨版本迁移。'}
