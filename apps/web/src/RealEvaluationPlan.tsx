import { useEffect, useState } from 'react'
import { API_BASE, apiJson } from './api'

type Protocol = {
  digest: string
  planned_attempts: number
  repeats: number
  notice: string
  cases: {
    id: string
    title: string
    request: string
    fixture: string
    checks: { id: string; action: string; expected: string }[]
  }[]
}
type Stats = {
  planned: number
  recorded_runs: number
  pending: number
  autonomous_passed: number
  first_passed: number
  assisted_passed: number
  unverified: number
  known_cost_cny: number
  cost_unknown_runs: number
  over_budget_runs: number
  interventions: number
  unknown_intervention_runs: number
  mean_seconds_known: number | null
  timed_runs: number
}
type Assessment = {
  notice: string
  conditions_frozen: boolean
  complete: boolean
  comparison_ready: boolean
  strategies: Record<string, Stats>
}

export default function RealEvaluationPlan() {
  const [protocol, setProtocol] = useState<Protocol | null>(null)
  const [result, setResult] = useState<Assessment | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  useEffect(() => {
    let gone = false
    apiJson<Protocol>('/evaluations/real-protocol')
      .then((value) => {
        if (!gone) setProtocol(value)
      })
      .catch(() => {
        if (!gone) setError('评测计划暂不可用。')
      })
    return () => {
      gone = true
    }
  }, [])
  async function assess(file: File) {
    setBusy(true)
    setError('')
    setResult(null)
    try {
      if (file.size > 1024 * 1024) throw new Error('结果文件最多 1 MiB。')
      const payload = JSON.parse(await file.text())
      setResult(
        await apiJson<Assessment>('/evaluations/real-assessment', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payload),
        }),
      )
    } catch (e) {
      setError(e instanceof Error ? e.message : '结果文件无法读取。')
    } finally {
      setBusy(false)
    }
  }
  return (
    <details className="expansion-panel">
      <summary>真实任务评测准备 · 尚未运行</summary>
      <p>{protocol?.notice}</p>
      <p>
        单角色和三角色使用相同任务、固定资料、检查和预算。每题每策略 {protocol?.repeats ?? 3} 次，共{' '}
        {protocol?.planned_attempts ?? 30} 次计划；没有启动或付费调用按钮。
      </p>
      {protocol?.cases.map((item) => (
        <details key={item.id}>
          <summary>{item.title}</summary>
          <p>{item.request}</p>
          <p>执行前准备：{item.fixture}</p>
          <ol>
            {item.checks.map((check) => (
              <li key={check.id}>
                {check.action} → {check.expected}
              </li>
            ))}
          </ol>
        </details>
      ))}
      <p>
        <a href={API_BASE + '/evaluations/real-template'} download>
          下载空白评测结果模板
        </a>
      </p>
      <label>
        核对已登记的结果（仅本机统计，不启动任务、不保存上传文件）
        <input
          type="file"
          accept=".json"
          disabled={busy}
          onChange={(event) => {
            const file = event.target.files?.[0]
            if (file) void assess(file)
            event.target.value = ''
          }}
        />
      </label>
      {result && (
        <div role="status">
          <p>{result.notice}</p>
          <p>
            条件{result.conditions_frozen ? '已登记' : '未完整登记'}；计划
            {result.complete ? '已全部登记结果' : '尚未完成，不作排名'}。
          </p>
          <p>
            {result.comparison_ready
              ? '登记条件齐全，可人工复核后作描述性对照；不证明统计显著性。'
              : '缺少记录、费用、介入信息或存在超预算结果时，不作公平性对照。'}
          </p>
          {Object.entries(result.strategies).map(([strategy, stats]) => (
            <div key={strategy}>
              <b>{strategy === 'single' ? '单角色' : '三角色'}</b>
              <p>
                已运行 {stats.recorded_runs}/{stats.planned}；待运行 {stats.pending}；自主通过{' '}
                {stats.autonomous_passed}；首次通过 {stats.first_passed}；人工辅助通过{' '}
                {stats.assisted_passed}；通过但证据不足 {stats.unverified}。
              </p>
              <p>
                已知估算费用 ¥{stats.known_cost_cny.toFixed(4)}（{stats.cost_unknown_runs}{' '}
                次费用未知，{stats.over_budget_runs} 次超过登记预算）；人工介入{' '}
                {stats.interventions} 次（{stats.unknown_intervention_runs}{' '}
                次运行未登记介入情况）；已知平均耗时 {stats.mean_seconds_known?.toFixed(1) ?? '—'}{' '}
                秒（{stats.timed_runs} 次）。
              </p>
            </div>
          ))}
        </div>
      )}
      {busy && <p role="status">正在核对本地结果…</p>}
      {error && (
        <p role="alert" className="inline-state error">
          {error}
        </p>
      )}
    </details>
  )
}
