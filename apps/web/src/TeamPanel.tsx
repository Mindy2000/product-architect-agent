import type { RunRecord } from './runTypes'
import { terminalStates } from './runTypes'

export const agentNames: Record<string, string> = {
  coordinator: '统一调度',
  planner: '产品规划',
  developer: '开发',
  reviewer: '审查测试',
}
const reviewNames: Record<string, string> = {
  in_progress: '审查中',
  ready_for_manual: '已提交，待人工验收',
  changes_requested: '需要修复',
  blocked: '无法继续',
  stale: '证据已失效',
}
const criterionNames: Record<string, string> = {
  supported: '有源码证据',
  gap: '发现缺口',
  manual: '待人工核对',
}
const roles = [
  { id: 'planner', duty: '拆解冻结需求，关联验收标准', permission: '只读资料与源码' },
  { id: 'developer', duty: '实现任务，依据审查意见修复', permission: '唯一可修改源码的角色' },
  { id: 'reviewer', duty: '独立读取实现，逐条审查', permission: '只读源码，运行固定检查' },
]
export default function TeamPanel({ run }: { run: RunRecord }) {
  if (run.orchestration_version !== 1) return null
  const running = !terminalStates.has(run.status)
  return (
    <section className="team-panel" aria-label="三智能体协作">
      <div className="execution-heading">
        <b>统一调度 · 三智能体协作</b>
        <span>
          共享步骤 {run.team_steps ?? 0} / {run.execution_step_limit ?? 40}
        </span>
      </div>
      <p className="team-note">
        按任务顺序协作，共用模型与费用上限。当前{running ? '执行' : '停留'}：
        {agentNames[run.active_agent ?? 'coordinator']}
      </p>
      <div className="team-roles">
        {roles.map((role) => (
          <article
            key={role.id}
            className={running && run.active_agent === role.id ? 'active' : ''}
          >
            <strong>
              {agentNames[role.id]}
              {running && run.active_agent === role.id ? ' · 进行中' : ''}
            </strong>
            <p>{role.duty}</p>
            <small>{role.permission}</small>
          </article>
        ))}
      </div>
      {run.review_status && (
        <p className="team-verdict">
          审查结论：{reviewNames[run.review_status] ?? run.review_status}
        </p>
      )}
      {run.review_report && (
        <details className="team-review">
          <summary>逐条智能体审查（不代替人工验收）</summary>
          <p>{run.review_report.summary}</p>
          <ol>
            {run.review_report.criteria.map((item) => (
              <li key={item.index}>
                <strong>{run.acceptance_criteria[item.index]}</strong>
                <span>{criterionNames[item.status]}</span>
                <p>{item.evidence}</p>
                {!!item.files.length && <small>{item.files.join('、')}</small>}
              </li>
            ))}
          </ol>
        </details>
      )}
      {!!run.agent_handoffs?.length && (
        <details>
          <summary>角色交接记录 · {run.agent_handoffs.length}</summary>
          <ol className="team-handoffs">
            {run.agent_handoffs.map((item) => (
              <li key={item.id}>
                <b>
                  {agentNames[item.from]} → {agentNames[item.to]}
                </b>
                <p>{item.summary}</p>
              </li>
            ))}
          </ol>
        </details>
      )}
    </section>
  )
}
