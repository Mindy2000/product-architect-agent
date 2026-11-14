import RealEvaluationPlan from './RealEvaluationPlan'
import { useEffect, useState } from 'react'
import { apiJson } from './api'
import MaintenanceJob, { startLocalJob } from './MaintenanceJob'
type Case = {
  case: string
  strategy: string
  status: string
  mechanism_passed: boolean
  steps: number
  repair_rounds: number
  seconds: number
  estimated_cost_cny: number
}
type Report = {
  id: string
  source_version: string
  suite_digest: string
  passed: boolean
  cases: Case[]
  notice: string
  created_at: string
}
type Data = {
  notice: string
  saved_reports: Report[]
  offline_reports: { id: string; status: string }[]
  history: {
    run_id: string
    name: string
    model: string
    status: string
    repair_rounds: number
    steps: number
    operator_assisted: boolean
    cost: { spent_cny_estimate: number; calls: number }
  }[]
}
export default function EvaluationCenter() {
  const [data, setData] = useState<Data | null>(null),
    [job, setJob] = useState(''),
    [error, setError] = useState(''),
    [busy, setBusy] = useState(false)
  const [left, setLeft] = useState(''),
    [right, setRight] = useState(''),
    [comparison, setComparison] = useState<{
      same_suite: boolean
      notice: string
      rows: {
        case: string
        strategy: string
        before_passed: boolean
        after_passed: boolean
        seconds_delta: number
        comparable: boolean
      }[]
      added: string[][]
      removed: string[][]
    } | null>(null)
  async function refresh() {
    setData(await apiJson<Data>('/evaluations'))
  }
  useEffect(() => {
    let gone = false
    apiJson<Data>('/evaluations')
      .then((d) => {
        if (!gone) {
          setData(d)
          const active = d.offline_reports.find((x) => ['queued', 'running'].includes(x.status))
          if (active) setJob(active.id)
        }
      })
      .catch((e) => {
        if (!gone) setError(e.message)
      })
    return () => {
      gone = true
    }
  }, [])
  return (
    <section className="expansion-panel">
      <h2>质量与成本评测</h2>
      <RealEvaluationPlan />
      <p>{data?.notice || '固定响应评测不调用付费模型。'}</p>
      <p>
        离线套件：任务管理、报名表单、仪表盘。比较40步标准策略与8步限额策略；另含坏参数、过期证据、澄清中断、规则冲突和无进展修复五项故障注入。使用独立副本顺序构建和测试，请先关闭预览并暂停开发。
      </p>
      <button
        disabled={busy || !!job}
        onClick={async () => {
          setBusy(true)
          setError('')
          try {
            const j = await startLocalJob({ kind: 'evaluation' })
            setJob(j.id)
          } catch (e) {
            setError(e instanceof Error ? e.message : '评测未开始')
          } finally {
            setBusy(false)
          }
        }}
      >
        运行本地离线对照评测
      </button>
      {job && (
        <MaintenanceJob
          id={job}
          onFinished={() => {
            setJob('')
            void refresh().catch((e) => setError(e.message))
          }}
        />
      )}
      <details>
        <summary>比较软件版本的离线报告</summary>
        {[
          ['基准报告', left, setLeft],
          ['对照报告', right, setRight],
        ].map(([label, value, setter]) => (
          <label key={String(label)}>
            {String(label)}
            <select
              value={String(value)}
              onChange={(e) => (setter as (value: string) => void)(e.target.value)}
            >
              <option value="">请选择</option>
              {data?.saved_reports.map((r) => (
                <option key={r.id} value={r.id}>
                  v{r.source_version} · {new Date(r.created_at).toLocaleString('zh-CN')}
                </option>
              ))}
            </select>
          </label>
        ))}
        <button
          disabled={!left || !right || left === right || busy}
          onClick={async () => {
            try {
              setComparison(await apiJson('/evaluations/compare?left=' + left + '&right=' + right))
            } catch (e) {
              setError(e instanceof Error ? e.message : '报告比较失败')
            }
          }}
        >
          比较报告
        </button>
        {comparison && (
          <>
            <p>{comparison.notice}</p>
            <p>
              新增 {comparison.added.map((v) => v.join(' / ')).join('、') || '无'}；移除{' '}
              {comparison.removed.map((v) => v.join(' / ')).join('、') || '无'}
            </p>
            {comparison.rows.map((r) => (
              <p key={r.case + r.strategy}>
                {r.case} · {r.strategy}：{r.before_passed ? '通过' : '未通过'} →{' '}
                {r.after_passed ? '通过' : '未通过'} ·{' '}
                {r.comparable
                  ? '条件一致，耗时差 ' + r.seconds_delta + ' 秒'
                  : '套件或环境不同，不作性能结论'}
              </p>
            ))}
          </>
        )}
      </details>
      {data?.saved_reports
        .slice()
        .reverse()
        .map((r) => (
          <details key={r.id}>
            <summary>
              {new Date(r.created_at).toLocaleString('zh-CN')} · v{r.source_version} ·{' '}
              {r.passed ? '机制检查通过' : '存在未通过项'}
            </summary>
            <p>{r.notice}</p>
            <small>套件指纹：{r.suite_digest.slice(0, 16)}</small>
            <div className="table-scroll">
              <table>
                <thead>
                  <tr>
                    <th>用例</th>
                    <th>策略</th>
                    <th>结果</th>
                    <th>机制检查</th>
                    <th>步骤</th>
                    <th>修复轮数</th>
                    <th>秒</th>
                    <th>费用</th>
                  </tr>
                </thead>
                <tbody>
                  {r.cases.map((x) => (
                    <tr key={x.case + x.strategy}>
                      <td>
                        {{
                          tasks: '任务管理',
                          form: '报名表单',
                          dashboard: '仪表盘',
                          malformed_arguments: '工具坏参数',
                          stale_evidence: '过期证据',
                          clarification_interrupt: '澄清中断',
                          rule_conflict: '规则冲突',
                          no_progress_repair: '无进展修复',
                        }[x.case] ?? x.case}
                      </td>
                      <td>
                        {{ 'standard-40': '标准 · 40步', 'bounded-8': '限额 · 8步' }[x.strategy] ??
                          x.strategy}
                      </td>
                      <td>
                        {{
                          completed: '执行完成',
                          failed_validation: '保留未完成',
                          paused: '已暂停',
                          interrupted: '已中断',
                        }[x.status] ?? x.status}
                      </td>
                      <td>{x.mechanism_passed ? '符合预期' : '不符合预期'}</td>
                      <td>{x.steps}</td>
                      <td>{x.repair_rounds}</td>
                      <td>{x.seconds}</td>
                      <td>¥{x.estimated_cost_cny}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </details>
        ))}
      <details>
        <summary>最近100个项目的实际运行记录</summary>
        <p>项目需求不同，这些是描述性记录，不是同题模型排名。“执行完成”不代表人工验收通过。</p>
        <div className="table-scroll">
          <table>
            <thead>
              <tr>
                <th>项目</th>
                <th>模型</th>
                <th>状态</th>
                <th>步骤/修复</th>
                <th>调用/估算费用</th>
                <th>人工辅助</th>
              </tr>
            </thead>
            <tbody>
              {data?.history.map((x) => (
                <tr key={x.run_id}>
                  <td>{x.name}</td>
                  <td>{x.model}</td>
                  <td>
                    {{
                      completed: '执行完成',
                      failed_validation: '保留未完成',
                      paused: '已暂停',
                      interrupted: '已中断',
                    }[x.status] ?? x.status}
                  </td>
                  <td>
                    {x.steps}/{x.repair_rounds}
                  </td>
                  <td>
                    {x.cost.calls}/¥{x.cost.spent_cny_estimate.toFixed(4)}
                  </td>
                  <td>{x.operator_assisted ? '有' : '未登记'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </details>
      {error && <p className="inline-state error">{error}</p>}
    </section>
  )
}
