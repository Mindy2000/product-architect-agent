import { useEffect, useState } from 'react'
import { API_BASE, apiJson } from './api'
export type Selection = {
  tag: string
  text: string
  selector: string
  rect: { x: number; y: number; width: number; height: number }
}
type Note = {
  id: string
  comment: string
  image: string
  candidates: string[]
  status?: string
  association?: {
    locations: { path: string; line: number; confidence: string }[]
    notice: string
    uncertain: boolean
  }
  selection: Selection
}
type Baseline = { id: string; label: string; run_id: string; image: string; route: string }
type Comparison = {
  current_evidence?: boolean
  id: string
  changed_ratio: number
  image: string
  diff_image: string
  meaning: string
  baseline: Baseline
}
export default function PreviewFeedback({
  runId,
  selection,
  hasFrame,
  onDraft,
}: {
  runId: string
  selection?: Selection | null
  hasFrame: boolean
  onDraft?: (ids: string[], text: string) => void
}) {
  const [notes, setNotes] = useState<Note[]>([]),
    [baselines, setBaselines] = useState<Baseline[]>([]),
    [comparisons, setComparisons] = useState<Comparison[]>([]),
    [comment, setComment] = useState(''),
    [label, setLabel] = useState(''),
    [chosen, setChosen] = useState(''),
    [selectedNotes, setSelectedNotes] = useState<string[]>([]),
    [error, setError] = useState(''),
    [busy, setBusy] = useState(false)
  const base = '/collaboration/projects/' + runId
  async function refresh() {
    const d = await apiJson<{ notes: Note[]; baselines: Baseline[]; comparisons: Comparison[] }>(
      base + '/evidence',
    )
    setNotes(d.notes)
    setBaselines(d.baselines)
    setComparisons(d.comparisons)
  }
  useEffect(() => {
    let gone = false
    apiJson<{ notes: Note[]; baselines: Baseline[]; comparisons: Comparison[] }>(base + '/evidence')
      .then((d) => {
        if (!gone) {
          setNotes(d.notes)
          setBaselines(d.baselines)
          setComparisons(d.comparisons)
        }
      })
      .catch((e) => {
        if (!gone) setError(e.message)
      })
    return () => {
      gone = true
    }
  }, [base])
  async function act(path: string, body: object) {
    setBusy(true)
    setError('')
    try {
      await apiJson(base + path, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      })
      await refresh()
    } catch (e) {
      setError(e instanceof Error ? e.message : '操作未完成')
    } finally {
      setBusy(false)
    }
  }
  useEffect(() => {
    const update = () => {
      void refresh().catch((e) => setError(e.message))
    }
    window.addEventListener('collaboration-changed', update)
    return () => window.removeEventListener('collaboration-changed', update)
  }, [base])
  const img = (rid: string, name: string) =>
    API_BASE + '/collaboration/projects/' + rid + '/images/' + name
  return (
    <div className="expansion-panel">
      <h3>页面修改意见</h3>
      {selection && (
        <p>
          已点选 {selection.tag}：{selection.text || '无文字区域'}
          <small> · 已保存页面位置</small>
        </p>
      )}
      <textarea
        aria-label="页面修改意见"
        placeholder="先在点选模式选择页面元素，再描述修改"
        value={comment}
        onChange={(e) => setComment(e.target.value)}
        maxLength={1200}
      />
      <button
        disabled={busy || !hasFrame || !selection || comment.trim().length < 3}
        onClick={() => void act('/notes', { comment })}
      >
        保存点选意见与截图
      </button>
      {notes.map((n) => (
        <div key={n.id}>
          <label>
            <input
              type="checkbox"
              checked={selectedNotes.includes(n.id)}
              disabled={busy || n.status === 'closed'}
              onChange={(e) =>
                setSelectedNotes(
                  e.target.checked
                    ? [...selectedNotes, n.id].slice(0, 8)
                    : selectedNotes.filter((x) => x !== n.id),
                )
              }
            />
            {n.comment}
          </label>
          {n.association && (
            <details>
              <summary>源码关联 · {n.association.uncertain ? '待核对候选' : '显式标记'}</summary>
              {n.association.locations.map((v, i) => (
                <p key={i}>
                  {v.path}:{v.line} · {v.confidence}
                </p>
              ))}
              <small>{n.association.notice}</small>
            </details>
          )}
          <small>候选文件：{n.candidates.join('、') || '需读取源码定位'}</small> ·{' '}
          <a href={img(runId, n.image)} target="_blank" rel="noreferrer">
            查看截图
          </a>
        </div>
      ))}
      {onDraft && (
        <button
          disabled={busy || !selectedNotes.length}
          onClick={() =>
            onDraft(
              selectedNotes,
              notes
                .filter((n) => selectedNotes.includes(n.id))
                .map((n) => n.comment)
                .join('\n'),
            )
          }
        >
          将选中意见带入修改需求
        </button>
      )}
      <details>
        <summary>本地视觉回归</summary>
        <p>
          将当前页面明确保存为基线，再比较相同页面与状态。动态数据、焦点和滚动也会产生差异；不调用视觉API，不自动判断美观。
        </p>
        <label>
          基线名称
          <input
            value={label}
            onChange={(e) => setLabel(e.target.value)}
            maxLength={100}
            placeholder="例如：首页空状态"
          />
        </label>
        <button
          disabled={busy || !hasFrame || !label.trim()}
          onClick={() => void act('/baselines', { label, confirmed: true })}
        >
          确认当前页面为新基线
        </button>
        <label>
          选择比较基线
          <select value={chosen} onChange={(e) => setChosen(e.target.value)}>
            <option value="">请选择</option>
            {baselines.map((b) => (
              <option key={b.id} value={b.id}>
                {b.label} · {b.route}
              </option>
            ))}
          </select>
        </label>
        <button
          disabled={busy || !hasFrame || !chosen}
          onClick={() => void act('/compare', { baseline_id: chosen })}
        >
          与当前页面比较
        </button>
        {comparisons
          .slice()
          .reverse()
          .map((x) => (
            <article key={x.id}>
              <b>
                {x.baseline.label} ·{' '}
                {x.current_evidence ? '当前源码证据' : '历史比较，当前依据未核对'} · 变化像素{' '}
                {(x.changed_ratio * 100).toFixed(2)}%
              </b>
              <div className="visual-comparison">
                <a href={img(x.baseline.run_id, x.baseline.image)} target="_blank" rel="noreferrer">
                  <img alt="视觉基线" src={img(x.baseline.run_id, x.baseline.image)} />
                </a>
                <a href={img(runId, x.diff_image)} target="_blank" rel="noreferrer">
                  <img alt="当前页面差异标记" src={img(runId, x.diff_image)} />
                </a>
              </div>
              <small>{x.meaning}</small>
              <button
                disabled={busy || !hasFrame || comment.trim().length < 3}
                onClick={() => void act('/notes', { comment, comparison_id: x.id })}
              >
                附带此差异保存修改意见
              </button>
            </article>
          ))}
      </details>
      {error && <p className="inline-state error">{error}</p>}
    </div>
  )
}
