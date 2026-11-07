import { API_BASE, apiFetch } from './api'
import { useState } from 'react'
const API = API_BASE + '/projects'
type Link = { criterion: number; task_id: string; files: string[]; note: string }
type RecordData = {
  page_sources: Record<string, string[]>
  page_routes: string[]
  source_files: string[]
  suggested_summary?: string
  suggested_links?: Link[]
  revision: number
  summary: string
  links: Link[]
  criteria: string[]
  tasks: { id: string; title: string }[]
  criteria_added: string[]
  criteria_removed: string[]
  baseline_frozen: boolean
  parent_run_id?: string
  events: { revision: number; created_at: string }[]
}
type Diff = { path: string; status: string; diff: string }
export default function ProjectMaintenance({ runId }: { runId: string }) {
  const [data, setData] = useState<RecordData | null>(null),
    [diff, setDiff] = useState<Diff[]>([]),
    [error, setError] = useState(''),
    [busy, setBusy] = useState(false),
    [notice, setNotice] = useState('')
  async function load() {
    setBusy(true)
    setError('')
    try {
      const r = await apiFetch(`${API}/${runId}/maintenance`)
      if (!r.ok) throw new Error('无法读取维护记录。')
      setData(await r.json())
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
    try {
      const r = await apiFetch(`${API}/${runId}/maintenance`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          revision: data.revision,
          summary: data.summary,
          links: data.links,
          page_sources: data.page_sources,
        }),
      })
      const value = await r.json()
      if (!r.ok) throw new Error(value.detail ?? '保存失败。')
      setData({ ...data, revision: value.revision })
      setNotice('维护记录已保存；不会改写冻结的开发方案。')
    } catch (e) {
      setError(e instanceof Error ? e.message : '保存失败')
    } finally {
      setBusy(false)
    }
  }
  async function loadDiff() {
    setError('')
    try {
      const r = await apiFetch(`${API}/${runId}/diff`)
      const value = await r.json()
      if (!r.ok) throw new Error(value.detail ?? '无法读取差异')
      setDiff(value.files)
    } catch (e) {
      setError(e instanceof Error ? e.message : '读取失败')
    }
  }
  function updateLink(index: number, patch: Partial<Link>) {
    if (data)
      setData({
        ...data,
        links: data.links.map((link, i) => (i === index ? { ...link, ...patch } : link)),
      })
  }
  return (
    <details
      className="project-maintenance"
      onToggle={(e) => {
        if (e.currentTarget.open && !data && !busy) void load()
      }}
    >
      <summary>项目维护记录与需求关联</summary>
      {error && <p role="alert">{error}</p>}
      {busy && <p>正在处理…</p>}
      {data && (
        <>
          <p>
            开发方案{data.baseline_frozen ? '已冻结' : '未记录冻结状态'} · 维护修订 {data.revision}
          </p>
          <label>
            项目维护摘要
            <textarea
              value={data.summary}
              maxLength={3000}
              onChange={(e) => setData({ ...data, summary: e.target.value })}
            />
          </label>
          <p>新增验收标准：{data.criteria_added.join('；') || '无'}</p>
          <p>移除验收标准：{data.criteria_removed.join('；') || '无'}</p>
          <button
            onClick={() =>
              setData({
                ...data,
                summary: data.summary || data.suggested_summary || '',
                links: data.links.length ? data.links : (data.suggested_links ?? []),
              })
            }
          >
            填入项目摘要和规划关联建议
          </button>
          <fieldset>
            <legend>页面与源码映射</legend>
            <p>选择每条路由的入口文件，用于核对间接影响；不会自动改写代码。</p>
            {data.page_routes?.map((route) => (
              <label key={route}>
                页面 {route}
                <select
                  aria-label={'页面入口 ' + route}
                  value={data.page_sources?.[route]?.[0] ?? ''}
                  onChange={(e) => {
                    const next = { ...data.page_sources }
                    if (e.target.value) next[route] = [e.target.value]
                    else delete next[route]
                    setData({ ...data, page_sources: next })
                  }}
                >
                  <option value="">尚未确认</option>
                  {data.source_files
                    ?.filter((name) => name.startsWith('src/'))
                    .map((name) => (
                      <option key={name}>{name}</option>
                    ))}
                </select>
              </label>
            ))}
          </fieldset>
          <p>以下为人工维护的追溯关联，不代表验收已通过。</p>
          {data.links.map((link, index) => (
            <fieldset key={index}>
              <label>
                验收标准
                <select
                  value={link.criterion}
                  onChange={(e) => updateLink(index, { criterion: Number(e.target.value) })}
                >
                  {data.criteria.map((criterion, i) => (
                    <option key={i} value={i}>
                      {criterion}
                    </option>
                  ))}
                </select>
              </label>
              <label>
                开发任务
                <select
                  value={link.task_id}
                  onChange={(e) => updateLink(index, { task_id: e.target.value })}
                >
                  {data.tasks.map((task) => (
                    <option key={task.id} value={task.id}>
                      {task.title}
                    </option>
                  ))}
                </select>
              </label>
              <label>
                关联文件（每行一个项目内路径）
                <textarea
                  value={link.files.join('\n')}
                  onChange={(e) =>
                    updateLink(index, { files: e.target.value.split('\n').filter(Boolean) })
                  }
                />
              </label>
              <label>
                关联说明
                <input
                  maxLength={1000}
                  value={link.note}
                  onChange={(e) => updateLink(index, { note: e.target.value })}
                />
              </label>
              <button
                type="button"
                onClick={() =>
                  setData({ ...data, links: data.links.filter((_, i) => i !== index) })
                }
              >
                移除此关联
              </button>
            </fieldset>
          ))}
          <button
            disabled={busy || !data.criteria.length || !data.tasks.length}
            onClick={() =>
              setData({
                ...data,
                links: [
                  ...data.links,
                  { criterion: 0, task_id: data.tasks[0].id, files: [], note: '' },
                ],
              })
            }
          >
            添加关联
          </button>
          <button disabled={busy} onClick={() => void save()}>
            保存维护记录
          </button>
          {notice && <p role="status">{notice}</p>}
          {data.parent_run_id && (
            <button onClick={() => void loadDiff()}>逐文件查看父子差异</button>
          )}
          {diff.map((file) => (
            <details key={file.path}>
              <summary>
                {file.path} · {file.status}
              </summary>
              <pre>{file.diff}</pre>
            </details>
          ))}
          <p>
            最近维护修订：
            {data.events.map((event) => `#${event.revision} ${event.created_at}`).join('；') ||
              '尚无记录'}
          </p>
        </>
      )}
    </details>
  )
}
