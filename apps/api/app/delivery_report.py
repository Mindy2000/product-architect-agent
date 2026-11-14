"""Delivery report formatting, separate from filesystem, container and Git execution."""
from .evidence_policy import report_notice
from .security_policy import redact


def render_report(run_id, state, evidence, web_sources, run_cost, team_export):
    spec = state.get("product_spec", {})
    repair_attempts = max(
        state.get("repair_round", 0),
        sum(1 for event in state.get("trace", []) if event.get("stage") == "repair" and event.get("status") == "running"),
    )
    report = [f"# {spec.get('product_name', '生成项目')} 交付报告", "", f"- 运行 ID：`{run_id}`", f"- 状态：`{state.get('status', 'unknown')}`", f"- 构建：`{state.get('build_status', 'unknown')}`", f"- Playwright：`{state.get('test_status', 'unknown')}`", f"- 自动修复尝试轮数：{repair_attempts} / 3", f"- 估算累计模型费用：¥{run_cost:.6f}", "", "## 产品摘要", "", spec.get("summary", ""), "", "## 验收标准", ""]
    if spec.get('application_type')=='fullstack_crud':
        report.extend(['','## 业务后端与数据','','- FastAPI + SQLite，受控CRUD数据结构生成。','- 业务数据库按项目保存，不包含在源码ZIP或源码版本恢复内。','- 测试使用临时数据库；真实项目库初始化/升级需显式执行。'])
    if state.get('runtime_version',1) >= 2:
        report.extend(['', '## 任务与执行', '', f"- 决策步数：{state.get('team_steps',state.get('executor_turns',0))} / {state.get('execution_step_limit',40)}", f"- 本次估算费用上限：¥{state.get('run_budget_cny',5)}"])
        report.extend(f"- {task['id']} [{task['status']}] {task['title']}；依赖：{', '.join(task.get('depends_on',[])) or '无'}；修改：{', '.join(task.get('changed_paths',[])) or '无'}" for task in state.get('executor_tasks',[]))
        report.extend(['', '## 验收标准', ''])
    if state.get('operator_assisted'):
        report.extend(['', '## 执行方式与限制', '', state.get('operator_note','本次由Codex辅助完成，不能视为执行器完全自主完成。'), '', '## 验收标准', ''])
    report.extend(['','## 验收证据（交付时快照）','',evidence['notice'],f"逐条确认：{evidence['counts']['passed']} / {evidence['counts']['total']}；未核对或已失效：{evidence['counts']['unverified']}",f"检查来源：{evidence['technical']['test_kind']}；独立测试不自动映射全部需求。"])
    labels={'passed':'已确认通过','failed':'已确认失败','stale':'证据已失效','unreviewed':'尚未核对'}
    report.extend(f"- [{labels[item['status']]}] {item['text']}" for item in evidence['criteria'])
    if web_sources:
        report.extend(['','## 公开网页参考来源（冻结快照，非验收证明）',''])
        report.extend('- '+item['title']+' · '+item['url']+' · SHA256 '+item['sha256'] for item in web_sources)
    report.extend(["", "## 浏览器测试结果（不等同全部需求验收）", "", redact(state.get("test_output", "未运行"))[-8000:], "", "## 构建结果", "", redact(state.get("build_output", "未运行"))[-8000:], "", "## 自动修复记录", ""])
    report.extend(f"- {event.get('summary', '')}" for event in state.get("trace", []) if event.get("stage") == "repair")
    if team_export:
        report.extend(['','## 三智能体协作','',team_export['notice'],f"审查状态：{state.get('review_status','未完成')}"])
        report.extend(f"- {item['from']} → {item['to']}：{item['summary']}" for item in state.get('agent_handoffs',[]))
        report.extend(f"- 标准 {item['index']+1} [{item['status']}] {item['evidence']}" for item in (state.get('review_report') or {}).get('criteria',[]))
    if state.get('last_error'):report.extend(['','## 停止或未完成原因','',state['last_error']])
    if state.get('review_issues'):
        report.extend(['','## 问题复查记录',''])
        report.extend(f"- {issue['id']} [{issue['status']}] {issue['evidence']}；审查引用位置：{', '.join(issue.get('files',[]))}" for issue in state['review_issues'])
    report.extend(report_notice(state))
    return report
