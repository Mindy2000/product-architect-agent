"""Versioned, local coordination rules; never increases a task's limits."""
from copy import deepcopy

class ReviewReserveReached(Exception):
    pass


def enabled(state):
    return state.get('team_policy_version') == 1


def reserve(state):
    total = max(0, float(state.get('run_budget_cny', 5)))
    minimum = 1.05 if state.get('model_name') == 'deepseek-v4-pro' else .75
    return {'steps': 3, 'estimated_cny': round(min(total, max(minimum, total * .2)), 6)}


def protect_stage(state, stage):
    return enabled(state) and (stage.startswith('executor-') or stage.startswith('agent-planner-'))


def coverage(state, plan, exceptions):
    """Every frozen criterion has an explicit owner; ownership is not evidence."""
    size = len(state['acceptance_criteria'])
    owners = {}
    for number, step in enumerate(plan.steps, 1):
        for index in step.acceptance_indices:
            if index < 0 or index >= size:
                raise ValueError('规划引用了未知验收标准。')
            owners.setdefault(index, {'index': index, 'owner': 'development', 'tasks': []})['tasks'].append(f'task-{number}')
    for entry in exceptions:
        item = entry.model_dump() if hasattr(entry, 'model_dump') else dict(entry)
        index = item['index']
        if index < 0 or index >= size or index in owners:
            raise ValueError('验收责任重复或序号不存在。')
        owners[index] = item
    missing = sorted(set(range(size)) - set(owners))
    if missing:
        raise ValueError('以下验收标准尚未分配责任（零基序号）：' + ', '.join(map(str, missing)))
    return [owners[index] for index in range(size)]


def no_change(state, basis, technical):
    previous=(state.get('review_history') or [None])[-1]
    return bool(previous and previous['report']['verdict']=='changes_requested'
                and previous['basis'].get('source_digest')==basis.get('source_digest')
                and previous['basis'].get('environment')==basis.get('environment')
                and previous.get('technical',{})==technical)


def reconcile(state, report, basis, technical=None):
    """Stable IDs follow criteria, and each review retains its own immutable snapshot."""
    episode = state.get('repair_round', 0)
    issues = {item['index']: deepcopy(item) for item in state.get('review_issues', [])}
    changed_paths=sorted({path for task in state.get('executor_tasks',[]) for path in task.get('changed_paths',[])})
    for item in report['criteria']:
        index = item['index']
        if item['status'] == 'gap' and index not in issues:
            issues[index] = {'id': f'criterion-{index+1}', 'index': index, 'status': 'open', 'first_round': episode, 'events': []}
        if index not in issues:
            continue
        issue = issues[index]
        issue['status'] = 'resolved' if item['status'] == 'supported' else 'open'
        issue['evidence'] = item['evidence']; issue['files'] = item.get('files', [])
        issue['events'] = [event for event in issue['events'] if event['round'] != episode]
        issue['events'].append({'round': episode, 'status': issue['status'], 'evidence': item['evidence'], 'files': item.get('files', []), 'changed_paths': changed_paths, 'source_digest': basis.get('source_digest')})
    snapshot = {'round': episode, 'report': deepcopy(report), 'basis': deepcopy(basis), 'technical': deepcopy(technical or {}), 'changed_paths': changed_paths}
    history = [item for item in state.get('review_history', []) if item['round'] != episode]
    open_issues = [item for item in issues.values() if item['status'] == 'open']
    reason = ''
    if report['verdict']=='changes_requested' and no_change({**state,'review_history':history},basis,technical or {}):
        reason = '上一轮退回后源码与运行环境没有变化，停止重复修复，保留未解决问题。'
    elif any(len(item['events']) >= 3 and all(event['status'] == 'open' for event in item['events'][-3:]) for item in open_issues):
        reason = '同一验收问题连续三轮审查仍未解决，提前停止，保留问题与证据。'
    return {'review_issues': list(issues.values()), 'review_history': history + [snapshot], 'review_stop_reason': reason}
