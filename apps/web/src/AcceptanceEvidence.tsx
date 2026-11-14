import { API_BASE, apiFetch, readJson } from './api'
import { useState } from 'react'
const API = API_BASE + '/projects'
type Review = {
  criterion: number
  method: 'manual' | 'independent_suite'
  result: 'passed' | 'failed'
  note: string
}
type Criterion = {
  index: number
  text: string
  status: string
  method: Review['method'] | null
  note: string
  candidate_task_ids: string[]
}
type Matrix = {
  coverage: {
    routes: string[]
    pending_routes: { route: string; reason: string }[]
    manual_checks: string[]
    interface_contract?: {
      scope: string
      checks: { kind: string }[]
    }
  }
  revision: number
  basis_hash: string
  notice: string
  review_status: string
  counts: { total: number; passed: number; failed: number; unverified: number }
  technical: {
    health_passed: boolean
    build_passed: boolean
    test_passed: boolean
    test_kind: string
    suite_sha256: string | null
  }
  criteria: Criterion[]
}
const statuses: Record<string, string> = {
  unreviewed: '尚未核对',
  stale: '旧确认已失效',
  passed: '已确认通过',
  failed: '已确认失败',
}
export default function AcceptanceEvidence({ runId }: { runId: string }) {
  const [data, setData] = useState<Matrix | null>(null),
    [reviews, setReviews] = useState<Record<number, Review>>({}),
    [error, setError] = useState(''),
    [notice, setNotice] = useState(''),
    [busy, setBusy] = useState(false)
  function apply(value: Matrix) {
    setData(value)
    setReviews(
      Object.fromEntries(
        value.criteria
          .filter((c) => ['passed', 'failed'].includes(c.status) && c.method)
          .map((c) => [
            c.index,
            {
              criterion: c.index,
              method: c.method!,
              result: c.status as Review['result'],
              note: c.note,
            },
          ]),
      ),
    )
  }
  async function load() {
    setBusy(true)
    setError('')
    try {
      const r = await apiFetch(`${API}/${runId}/acceptance`)
      apply(await readJson<Matrix>(r, '验收证据暂不可读'))
    } catch (e) {
      setError(e instanceof Error ? e.message : '读取失败')
    } finally {
      setBusy(false)
    }
  }
  async function save() {
    if (!data) return
    setBusy(true)
    setError('')
    setNotice('')
    try {
      const r = await apiFetch(`${API}/${runId}/acceptance`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          revision: data.revision,
          expected_basis_hash: data.basis_hash,
          reviews: Object.values(reviews),
        }),
      })
      apply(await readJson<Matrix>(r, '请完整填写每条核对说明'))
      setNotice('已保存与当前源码绑定的验收记录。源码、需求或独立测试变化后，对应确认将失效。')
    } catch (e) {
      setError(e instanceof Error ? e.message : '保存失败')
    } finally {
      setBusy(false)
    }
  }
  function choose(index: number, value: string) {
    setReviews((previous) => {
      const next = { ...previous }
      if (!value) delete next[index]
      else {
        const [method, result] = value.split(':')
        next[index] = {
          criterion: index,
          method: method as Review['method'],
          result: result as Review['result'],
          note: previous[index]?.note ?? '',
        }
      }
      return next
    })
  }
  return (
    <details
      className="project-maintenance"
      onToggle={(e) => {
        if (e.currentTarget.open && !data && !busy) void load()
      }}
    >
      <summary>逐条验收与独立证据</summary>
      <p>运行状态、自动测试与需求验收分别记录。人工确认仅由你主动保存，Agent 不会代为勾选。</p>
      <button disabled={busy} onClick={() => void load()}>
        重新读取验收证据
      </button>
      {error && <p role="alert">{error}</p>}
      {data && (
        <>
          <p>{data.notice}</p>
          <p role="status">
            逐条确认 {data.counts.passed}/{data.counts.total} · 失败 {data.counts.failed} ·
            未核对或失效 {data.counts.unverified}
          </p>
          <p>
            当前源码构建：{data.technical.build_passed ? '通过' : '无有效通过证据'}；测试：
            {data.technical.test_kind === 'independent_suite'
              ? '固定独立套件通过'
              : data.technical.test_kind === 'generated'
                ? '生成测试通过，覆盖范围须核对'
                : '无有效通过证据'}
            ；固定页面与控件检查：{data.technical.health_passed ? '通过' : '无有效通过证据'}。
          </p>
          <p>
            固定检查路径：{data.coverage.routes.join('、')}
            。每条路径使用新的浏览器状态，检查挂载、异常、溢出、刷新与键盘焦点；不自动确认空状态和错误提示的业务含义。
          </p>
          {data.coverage.pending_routes.map((item) => (
            <p key={item.route}>
              待核对路径 {item.route}：{item.reason}
            </p>
          ))}
          {data.coverage.interface_contract && (
            <p>
              已纳入独立检查：
              {data.coverage.interface_contract.checks
                .map((item) =>
                  item.kind === 'crud_task_toggle'
                    ? '勾选及时更新、保存失败回滚、重试与刷新持久化'
                    : '删除按钮的完整名称、逐行删除与刷新持久化',
                )
                .join('；')}
              。{data.coverage.interface_contract.scope}。
            </p>
          )}
          <p>仍需逐条核对：{data.coverage.manual_checks.join('；')}。</p>
          {data.criteria.map((item) => (
            <fieldset key={item.index} disabled={busy}>
              <legend>
                {item.index + 1}. {item.text}
              </legend>
              <p>{statuses[item.status] ?? item.status}</p>
              {item.candidate_task_ids.length > 0 && (
                <small>
                  规划关联任务：{item.candidate_task_ids.join('、')}；此关联不代表测试已覆盖。
                </small>
              )}
              <label>
                核对方式
                <select
                  aria-label={`验收方式 ${item.index + 1}`}
                  value={
                    reviews[item.index]
                      ? `${reviews[item.index].method}:${reviews[item.index].result}`
                      : ''
                  }
                  onChange={(e) => choose(item.index, e.target.value)}
                >
                  <option value="">暂不确认</option>
                  <option value="manual:passed">我已实际核对，通过</option>
                  <option value="manual:failed">我已实际核对，失败</option>
                  {data.technical.suite_sha256 && (
                    <option value="independent_suite:passed">关联当前独立套件通过证据</option>
                  )}
                </select>
              </label>
              {reviews[item.index] && (
                <label>
                  实际操作或测试对应说明
                  <textarea
                    aria-label={`验收说明 ${item.index + 1}`}
                    minLength={5}
                    maxLength={1000}
                    value={reviews[item.index].note}
                    onChange={(e) =>
                      setReviews((previous) => ({
                        ...previous,
                        [item.index]: { ...previous[item.index], note: e.target.value },
                      }))
                    }
                  />
                </label>
              )}
            </fieldset>
          ))}
          <button
            disabled={busy || Object.values(reviews).some((r) => r.note.trim().length < 5)}
            onClick={() => void save()}
          >
            保存逐条验收记录
          </button>
          <a className="workbench-download" href={`${API}/${runId}/acceptance/export`} download>
            导出当前验收证据 JSON
          </a>
          <p>
            最终状态：
            {data.review_status === 'confirmed'
              ? '当前源码技术检查通过，全部标准已逐条确认'
              : data.review_status === 'failed'
                ? '存在已确认失败的标准'
                : '仍需补充验收证据'}
            。交付 ZIP 和报告保留交付时快照，最新记录以此处导出为准。
          </p>
        </>
      )}
      {notice && <p role="status">{notice}</p>}
    </details>
  )
}
