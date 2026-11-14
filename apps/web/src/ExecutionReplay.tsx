import BranchEditor from './BranchEditor'
import { useState } from 'react'
import { apiJson, API_BASE } from './api'
type Timeline = {
  events: {
    kind: string
    id: string
    at?: string
    role?: string
    tool?: string
    revision?: number
  }[]
  points: { id: string; label: string }[]
  notice: string
  status: string
  stop_reason?: string
  branches: Result[]
}
type Result = {
  id: string
  created_at: string
  same_source: boolean
  notice: string
  branches: {
    label: string
    static_checks?: { total: number; passed: number }
    steps: number
    stop_reason: string
    source_digest: string
    changed_files: string[]
    diff: string
    events: object[]
  }[]
}
const preset = JSON.stringify(
  [
    {
      label: '读取后完成',
      decisions: [{ action: 'read_file', path: 'src/App.tsx' }, { action: 'finish' }],
    },
    {
      label: '读取后停止',
      decisions: [{ action: 'read_file', path: 'src/App.tsx' }, { action: 'stop' }],
    },
  ],
  null,
  2,
)
export default function ExecutionReplay({ runId }: { runId: string }) {
  const [data, setData] = useState<Timeline | null>(null),
    [point, setPoint] = useState('current'),
    [binding, setBinding] = useState<{
      token: string
      basis: object
      receipts?: { id: string; tool: string; sha256: string }[]
      files: { path: string; sha256: string }[]
    } | null>(null),
    [decisions, setDecisions] = useState(preset),
    [steps, setSteps] = useState(8),
    [error, setError] = useState(''),
    [busy, setBusy] = useState(false)
  const base = '/replay/' + runId
  async function act(fn: () => Promise<void>) {
    setBusy(true)
    setError('')
    try {
      await fn()
    } catch (e) {
      setError(e instanceof Error ? e.message : '操作失败')
    } finally {
      setBusy(false)
    }
  }
  return (
    <details className="expansion-panel">
      <summary>执行时间轴与固定决策分支</summary>
      <p>
        可查看实际输入、响应、工具回执和文件差异。分支仅在独立文本副本中执行固定读取、补丁、结束或停止，不调用模型，不执行生成代码或原任务副作用。
      </p>
      <button
        disabled={busy}
        onClick={() =>
          void act(async () => {
            setData(await apiJson<Timeline>(base))
            setBinding(null)
          })
        }
      >
        读取执行记录
      </button>
      {data && (
        <>
          <p>
            状态：{data.status} · {data.stop_reason || '未登记额外停止原因'}
          </p>
          <p>{data.notice}</p>
          <ol className="replay-timeline">
            {data.events.map((e, i) => (
              <li key={e.kind + e.id + i}>
                <details>
                  <summary>
                    {
                      {
                        agent: '智能体响应',
                        tool: '工具回执',
                        change: '源码修改',
                        trace: '执行事件',
                      }[e.kind]
                    }{' '}
                    · {e.role || e.tool || e.id} {e.at || ''}
                  </summary>
                  <pre>{JSON.stringify(e, null, 2)}</pre>
                </details>
              </li>
            ))}
          </ol>
          <label>
            源码回放位置
            <select
              aria-label="源码回放位置"
              value={point}
              onChange={(e) => {
                setPoint(e.target.value)
                setBinding(null)
              }}
            >
              {data.points.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.label}
                </option>
              ))}
            </select>
          </label>
          <button
            disabled={busy}
            onClick={() =>
              void act(async () =>
                setBinding(await apiJson(base + '/binding?point=' + encodeURIComponent(point))),
              )
            }
          >
            绑定检查点、回执及原限额
          </button>
          {binding && (
            <>
              <details>
                <summary>绑定依据与文件版本</summary>
                <pre>{JSON.stringify(binding, null, 2)}</pre>
              </details>
              <BranchEditor
                key={binding.token}
                value={decisions}
                onChange={setDecisions}
                files={binding.files}
                receipts={binding.receipts}
                disabled={busy}
              />
              <details>
                <summary>高级：固定决策 JSON</summary>
                <label>
                  两个固定决策分支（JSON）
                  <textarea
                    className="source-editor"
                    value={decisions}
                    onChange={(e) => setDecisions(e.target.value)}
                    maxLength={120000}
                  />
                </label>
              </details>
              <p>
                允许读取/修改源码、纯文本断言、读取冻结回执或结束。apply_changes
                使用受控文件操作及绑定的 expected_sha256；不允许命令或外部工具。
              </p>
              <label>
                每分支步骤上限
                <input
                  type="number"
                  min={1}
                  max={32}
                  value={steps}
                  onChange={(e) => setSteps(Number(e.target.value))}
                />
              </label>
              <button
                disabled={busy}
                onClick={() =>
                  void act(async () => {
                    await apiJson(base + '/branches', {
                      method: 'POST',
                      headers: { 'Content-Type': 'application/json' },
                      body: JSON.stringify({
                        point,
                        token: binding.token,
                        step_limit: steps,
                        branches: JSON.parse(decisions),
                      }),
                    })
                    setData(await apiJson(base))
                    setBinding(null)
                  })
                }
              >
                在独立副本比较两个分支
              </button>
            </>
          )}
          {data.branches
            .slice()
            .reverse()
            .map((r) => (
              <article className="rule-card" key={r.id}>
                <b>
                  {new Date(r.created_at).toLocaleString('zh-CN')} ·{' '}
                  {r.same_source ? '两个分支源码相同' : '两个分支源码不同'}
                </b>
                <p>{r.notice}</p>
                <a href={API_BASE + base + '/branches/' + r.id + '/export'} download>
                  导出脱敏分支记录
                </a>
                <div className="template-grid">
                  {r.branches.map((b, i) => (
                    <div key={i}>
                      <h4>{b.label}</h4>
                      <p>
                        {b.steps} 步 · {b.stop_reason}
                      </p>
                      <small>{b.source_digest.slice(0, 16)}</small>
                      {b.static_checks && (
                        <p>
                          源码断言：{b.static_checks.passed}/{b.static_checks.total}{' '}
                          通过（不等于业务验收）
                        </p>
                      )}
                      <p>{b.changed_files.join('、') || '没有源码修改'}</p>
                      <details>
                        <summary>文件差异与操作证据</summary>
                        <pre>{b.diff || '无差异'}</pre>
                        <pre>{JSON.stringify(b.events, null, 2)}</pre>
                      </details>
                    </div>
                  ))}
                </div>
              </article>
            ))}
        </>
      )}
      {error && <p className="inline-state error">{error}</p>}
    </details>
  )
}
