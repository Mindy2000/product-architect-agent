import { useEffect, useState } from 'react'
import { apiJson } from './api'
type RecordItem = {
  id: string
  kind: string
  revision?: number
  comment?: string
  label?: string
  archived?: boolean
  status?: string
  created_at: string
  implementation?: { run_id: string; source_digest: string }
  status_history?: { status: string; at: string; explanation: string }[]
}
const states: Record<string, string> = {
  pending: '待处理',
  adopted: '已采用',
  implemented: '已实施',
  awaiting_confirmation: '待人工确认',
  closed: '已关闭',
}
const next: Record<string, string[]> = {
  pending: ['adopted'],
  adopted: ['pending', 'implemented'],
  implemented: ['adopted', 'awaiting_confirmation'],
  awaiting_confirmation: ['implemented', 'closed'],
  closed: ['pending'],
}
export default function EvidenceManager({ runId }: { runId: string }) {
  const [items, setItems] = useState<RecordItem[]>([]),
    [q, setQ] = useState(''),
    [archived, setArchived] = useState(false),
    [error, setError] = useState(''),
    [busy, setBusy] = useState(false)
  const [chosen, setChosen] = useState<RecordItem | null>(null),
    [versions, setVersions] = useState<{ run_id: string; name: string; status: string }[]>([]),
    [version, setVersion] = useState(''),
    [status, setStatus] = useState('adopted'),
    [explanation, setExplanation] = useState('')
  const [deletion, setDeletion] = useState<{
    item: RecordItem
    can_delete: boolean
    token: string
    references: { type: string; id: string }[]
  } | null>(null)
  const base = '/collaboration/projects/' + runId
  async function refresh() {
    setItems(
      (
        await apiJson<{ items: RecordItem[] }>(
          base + '/records?q=' + encodeURIComponent(q) + '&archived=' + archived,
        )
      ).items,
    )
  }
  useEffect(() => {
    let gone = false
    apiJson<{ items: RecordItem[] }>(
      base + '/records?q=' + encodeURIComponent(q) + '&archived=' + archived,
    )
      .then((v) => {
        if (!gone) setItems(v.items)
      })
      .catch((e) => {
        if (!gone) setError(e.message)
      })
    return () => {
      gone = true
    }
  }, [base, q, archived])
  async function act(fn: () => Promise<void>) {
    setBusy(true)
    setError('')
    try {
      await fn()
      await refresh()
      window.dispatchEvent(new Event('collaboration-changed'))
    } catch (e) {
      setError(e instanceof Error ? e.message : '操作失败')
    } finally {
      setBusy(false)
    }
  }
  const post = (path: string, body: object) =>
    apiJson(base + path, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    })
  return (
    <details className="expansion-panel">
      <summary>资料管理与意见闭环</summary>
      <p>归档释放活动条数。被历史方案、任务或比较引用的记录保留，不能删除。关闭意见需人工确认。</p>
      <input
        aria-label="搜索协作资料"
        placeholder="搜索意见、基线名称或日期"
        value={q}
        onChange={(e) => setQ(e.target.value)}
        maxLength={200}
      />
      <label>
        <input type="checkbox" checked={archived} onChange={(e) => setArchived(e.target.checked)} />
        查看已归档
      </label>
      {items.map((v) => (
        <article key={v.id} className="rule-card">
          <b>
            {{ note: '页面意见', baseline: '视觉基线', comparison: '视觉比较' }[v.kind]} ·{' '}
            {v.comment || v.label || v.id.slice(0, 8)}
          </b>
          <small>
            {v.kind === 'note' ? states[v.status || 'pending'] : ''} ·{' '}
            {new Date(v.created_at).toLocaleString('zh-CN')}
          </small>
          {v.implementation && (
            <p>
              实施版本 {v.implementation.run_id.slice(0, 8)} · 记录时源码{' '}
              {v.implementation.source_digest.slice(0, 12)}（历史确认，不自动替代当前版本验收）
            </p>
          )}
          <div className="preview-tools">
            <button
              disabled={busy}
              onClick={() =>
                void act(async () => {
                  await post('/records/' + v.kind + '/' + v.id + '/archive', {
                    archived: !v.archived,
                    revision: v.revision || 1,
                  })
                })
              }
            >
              {v.archived ? '还原归档' : '归档'}
            </button>
            <button
              disabled={busy}
              onClick={() =>
                void act(async () => {
                  const p = await apiJson<{
                    can_delete: boolean
                    token: string
                    references: { type: string; id: string }[]
                  }>(base + '/records/' + v.kind + '/' + v.id + '/deletion')
                  setDeletion({ ...p, item: v })
                })
              }
            >
              查看删除影响
            </button>
            {v.kind === 'note' && (
              <button
                disabled={busy}
                onClick={() =>
                  void act(async () => {
                    const data = await apiJson<{
                      items: { run_id: string; name: string; status: string }[]
                    }>(base + '/notes/' + v.id + '/versions')
                    setVersions(data.items)
                    setChosen(v)
                    setStatus(next[v.status || 'pending'][0])
                    setVersion(v.implementation?.run_id || '')
                    setExplanation('')
                  })
                }
              >
                推进或重新打开意见
              </button>
            )}
          </div>
          {!!v.status_history?.length && (
            <details>
              <summary>状态历史</summary>
              {v.status_history.map((h, i) => (
                <p key={i}>
                  {states[h.status]} · {h.explanation} · {new Date(h.at).toLocaleString('zh-CN')}
                </p>
              ))}
            </details>
          )}
        </article>
      ))}
      {chosen && (
        <div className="clarification-card">
          <b>{chosen.comment}</b>
          <label>
            目标状态
            <select value={status} onChange={(e) => setStatus(e.target.value)}>
              {next[chosen.status || 'pending'].map((s) => (
                <option key={s} value={s}>
                  {states[s]}
                </option>
              ))}
            </select>
          </label>
          {['implemented', 'awaiting_confirmation', 'closed'].includes(status) && (
            <label>
              实施版本
              <select value={version} onChange={(e) => setVersion(e.target.value)}>
                <option value="">选择冻结了本条意见的版本</option>
                {versions.map((v) => (
                  <option key={v.run_id} value={v.run_id}>
                    {v.name} · {v.run_id.slice(0, 8)} · {v.status}
                  </option>
                ))}
              </select>
            </label>
          )}
          <textarea
            aria-label="意见状态说明"
            placeholder="说明采用、实施依据或人工确认结果"
            value={explanation}
            onChange={(e) => setExplanation(e.target.value)}
            maxLength={600}
          />
          <button
            disabled={busy || explanation.trim().length < 3}
            onClick={() =>
              void act(async () => {
                await post('/notes/' + chosen.id + '/status', {
                  revision: chosen.revision || 1,
                  status,
                  implementation_run_id: version || null,
                  explanation,
                  confirmed: status === 'closed',
                })
                setChosen(null)
              })
            }
          >
            {status === 'closed' ? '我已核对，确认关闭意见' : '保存状态'}
          </button>
          <button onClick={() => setChosen(null)}>取消</button>
        </div>
      )}
      {deletion && (
        <div className="clarification-card">
          <b>删除影响预览</b>
          <p>
            {deletion.can_delete
              ? '未发现引用；确认后删除记录及对应截图，不能撤销。'
              : '存在引用，只能归档。'}
          </p>
          {deletion.references.map((v, i) => (
            <p key={i}>
              {v.type} · {v.id.slice(0, 8)}
            </p>
          ))}
          <button
            disabled={busy || !deletion.can_delete}
            onClick={() =>
              void act(async () => {
                await post('/records/' + deletion.item.kind + '/' + deletion.item.id + '/delete', {
                  token: deletion.token,
                  confirmed: true,
                })
                setDeletion(null)
              })
            }
          >
            确认删除这条记录
          </button>
          <button onClick={() => setDeletion(null)}>取消</button>
        </div>
      )}
      {error && <p className="inline-state error">{error}</p>}
    </details>
  )
}
