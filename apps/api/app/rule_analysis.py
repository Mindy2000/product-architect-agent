"""Deterministic scope overlap and explicit-value diagnostics; never rewrites rules."""
def overlap(a, b):
    left, right = a.get('scope_type', 'project'), b.get('scope_type', 'project')
    if 'project' in (left, right):
        return '项目通用规则与所选范围重叠'
    if left == right:
        common = sorted(set(a.get('targets', [])) & set(b.get('targets', [])))
        return '相同范围：' + '、'.join(common) if common else None
    return '不同类型范围可能同时作用于同一页面或任务，需人工核对'


def analyze(rules):
    warnings = []
    for index, a in enumerate(rules):
        for b in rules[index + 1:]:
            scope = overlap(a, b)
            if not scope:
                continue
            base = {'ids': [a['id'], b['id']], 'titles': [a.get('title', a['id']), b.get('title', b['id'])], 'scope': scope,
                    'priorities': [a.get('priority', 0), b.get('priority', 0)],
                    'resolution': '优先级仅作处理顺序提示，不自动覆盖；请修订或取消本次选择后确认。'}
            if a.get('content', '').strip() == b.get('content', '').strip():
                warnings.append({**base, 'kind': 'duplicate', 'message': '重叠范围的正文重复，可人工合并'})
            for key, value in a.get('constraints', {}).items():
                if key not in b.get('constraints', {}):
                    continue
                other = b['constraints'][key]
                if value != other:
                    warnings.append({**base, 'kind': 'conflict', 'key': key, 'values': [value, other],
                                     'message': '重叠范围的结构化要求不同'})
                elif a.get('scope_type', 'project') != b.get('scope_type', 'project'):
                    warnings.append({**base, 'kind': 'redundant', 'key': key, 'values': [value, other],
                                     'message': '局部要求与通用/其他范围要求一致；可人工检查是否冗余'})
    return warnings
