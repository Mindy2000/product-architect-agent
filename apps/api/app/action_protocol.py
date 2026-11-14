"""Versioned action guidance; strict validation and permissions remain authoritative."""
from copy import deepcopy


def enabled(state):
    return state.get('agent_protocol_version') in (1, 2, 3)


def coverage_repair(state):
    return state.get('agent_protocol_version') in (2, 3)


def closeout_repair(state):
    return state.get('agent_protocol_version') == 3


CLOSEOUT_GUIDANCE = (
    '按最少的完整功能任务推进，不把同一页面的来源区、样式和测试留到预算末尾。'
    '先实现当前任务全部验收点并写测试，再集中构建/测试；未改源码且检查已通过时不要重复检查。'
    'delivery_budget列出所有未完成任务和建议工具步数，必须为每项complete_task保留一步。'
    '实现齐备就complete_task交接，不因重复检查拖到预留边界；不能把未实现内容声明完成。'
    '最后一项complete_task会自动进入系统构建/测试/审查，无需另发finish，完成声明不是验收通过。'
)


class ActionFormatError(ValueError):
    """Safe, fixed diagnostics: never include raw provider output."""


class ActionSchemaMixin:
    @classmethod
    def model_json_schema(cls, *args, **kwargs):
        schema = super().model_json_schema(*args, **kwargs)
        properties = schema['properties']
        branches = []
        for action, fields in cls.action_shapes:
            names = ['action', 'summary', *fields]
            props = {name: deepcopy(properties[name]) for name in names}
            props['action'] = {'const': action, 'type': 'string'}
            for name in fields:
                value = props[name]
                if 'anyOf' in value:
                    options = [item for item in value['anyOf'] if item.get('type') != 'null']
                    if len(options) == 1:
                        props[name] = options[0]
                props[name].pop('default', None)
            branches.append({'type': 'object', 'properties': props,
                             'required': names, 'additionalProperties': False})
        definitions = schema.get('$defs', {})
        needed = {}
        def collect(value):
            if isinstance(value, dict):
                reference = value.get('$ref', '')
                if reference.startswith('#/$defs/'):
                    name = reference.removeprefix('#/$defs/')
                    if name not in needed:
                        needed[name] = definitions[name]
                        collect(needed[name])
                for child in value.values():collect(child)
            elif isinstance(value, list):
                for child in value:collect(child)
        collect(branches)
        return {'title': schema.get('title', cls.__name__), 'oneOf': branches,
                **({'$defs': needed} if needed else {})}


EMPTY_LIST_GUIDANCE = (
    '数据记录列表的li只表示真实记录；暂无记录、加载中或错误提示放在ul/ol之外，'
    '不能伪装成记录行。空数据时保留有可访问名称的列表，记录行数为0；'
    '测试和审查同时核对空态提示、记录行数与保存结果。此约定不约束导航菜单。'
)


GUIDANCE = (
    '一次仅返回一个JSON对象，只选一种动作。禁止数组、多个并列JSON、解释文字或同时提交工具和结论。'
    'tool动作必须有tool注册名和arguments对象；不要省略tool，也不要仅写arguments。'
    '只提交当前动作Schema列出的字段，其他字段省略。'
    '工具示例：{"action":"tool","summary":"读取关键实现",'
    '"tool":"project.read_file","arguments":{"path":"src/App.tsx"}}。'
    '开发角色的工具动作还必须包含当前task_id。'
    '失败提示指明问题后先解决该问题，不原样反复提交。'
) + EMPTY_LIST_GUIDANCE


INTERFACE_GUIDANCE = (
    '冻结标准中的输入标签、按钮名称、aria-label与动态拼接方式必须逐字保留；'
    '不得自行添加“任务”、冒号、空格或标题后缀。需要额外说明时用相邻正文，不改变可访问名称。'
    '生成测试使用同一约定，重复记录先限定所在列表和li，再按role/name/exact:true定位。'
    '文件名可能同时出现在正文、下载链接和删除按钮，不用宽泛getByText定位。'
    '空ul可能没有可见高度；核对存在且li数量为0、列表外提示可见，不用空ul的toBeVisible代替空态验收。'
    '异步CRUD须等响应成功并核对界面结果后再刷新，刷新后再次核对数据。'
    '受控checkbox的onChange须立即用event.currentTarget.checked更新本地状态；使用同步ref锁防重复写，'
    '保存中禁用冲突操作，成功采用后端回执，失败回滚并显示错误，finally释放锁。不要等网络后才更新checked。'
    '检查延迟响应、失败回滚、重试、勾选与取消后的刷新持久化。固定控件检查失败时按精确名称及交互证据修复实现，不能只修改生成测试。'
    '测试失败先区分实现与冻结约定不符、定位歧义和真实业务错误；不能放宽既定验收来求通过。'
)

PATCH_GUIDANCE = (
    '局部修复优先project.patch_file：一次一个文件、一个唯一文本替换，'
    'arguments仅含path、expected_sha256、before、after。before和after是同一个对象的两个字符串字段，'
    '不可遗漏after字段名，不在after前嵌套额外对象。所有双引号、反斜杠与换行须正确JSON转义。'
)


def interface_contract(state):
    from . import interface_checks
    # Preserve original wording; this is a navigation aid, not an inferred new spec.
    criteria = state.get('acceptance_criteria') or []
    return {'criteria': [{'index': i, 'text': text} for i, text in enumerate(criteria)
                         if any(word in text for word in ('标签', '按钮', 'aria-label', '名称', '输入框', 'checkbox'))],
            'data_notes': (state.get('product_spec') or {}).get('data_notes', []),
            'fixed_interface_checks': interface_checks.contract(state['run_id']) if state.get('run_id') else None,
            'policy': INTERFACE_GUIDANCE}
