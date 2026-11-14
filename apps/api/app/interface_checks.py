"""Conservative, server-owned checks compiled from confirmed interface clauses.

This is a small grammar, not general semantic acceptance. Unknown/ambiguous
contracts remain manual. Generated source/tests cannot choose or edit checks.
"""
import json
import re
from . import runner, store


def contract(run_id):
    state = store.get_run(run_id) or {}
    spec = state.get('product_spec') or {}
    criteria = state.get('acceptance_criteria') or []
    clauses = [part.strip() for text in criteria if isinstance(text, str)
               for part in re.split('[，；。、]', text)]
    result = {'version': 1, 'checks': [], 'scope': '已确认单页任务列表中的明确控件约定；不代表全部需求验收',
              'manual_required': True}
    if not state.get('requirements_confirmed') or len(spec.get('page_details', [])) != 1 or spec['page_details'][0].get('route') != '/':
        return result
    # Only positive, complete clauses; do not infer from quoted source documents,
    # examples, arbitrary prose, generated tests, or negated requirements.
    common = {'route': '/', 'input': '任务标题', 'list': '任务列表'}
    if (spec.get('application_type') == 'frontend'
            and '输入标签任务标题' in clauses and '按钮添加任务' in clauses
            and 'ul的aria-label为任务列表' in clauses
            and '每行有删除+标题按钮' in clauses
            and '数据只在localStorage' in clauses and '刷新后保留' in clauses
            and '删除后刷新仍不存在' in clauses):
        result['checks'].append({**common, 'kind': 'local_task_delete', 'add': '添加任务', 'delete_prefix': '删除'})
    schema = spec.get('crud_schema') or {}
    entity = next((e for e in schema.get('entities', []) if e.get('name') == 'tasks'), {})
    fields = {f.get('name'): f.get('type') for f in entity.get('fields', [])}
    if (spec.get('application_type') == 'fullstack_crud' and fields.get('title') == 'text' and fields.get('done') == 'boolean'
            and '标签任务标题的输入框和新增任务按钮' in clauses
            and 'aria-label=任务列表的ul/li表示真实记录' in clauses
            and 'checkbox标签完成+任务标题' in clauses
            and '新增默认done=false' in clauses and '更新与完成状态刷新后保留' in clauses):
        result['checks'].append({**common, 'kind': 'crud_task_toggle', 'add': '新增任务', 'checkbox_prefix': '完成'})
    if result['checks']:
        result['source_criteria'] = criteria
    return result


def script(run_id):
    value = contract(run_id)
    if not value['checks']:
        return ''
    template = (runner.WORKER_CONTEXT.parent / 'interface-checks.ts').read_text()
    return '\n' + template.replace('/* PA_INTERFACE_CHECKS */ []', json.dumps(value['checks'], ensure_ascii=False))
