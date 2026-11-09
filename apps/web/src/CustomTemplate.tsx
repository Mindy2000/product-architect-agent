import { useState } from 'react'
import { apiJson } from './api'
type Candidate = {
  source_digest: string
  files: { path: string; content: string; blocked: boolean; bytes: number }[]
  spec: Record<string, unknown> | null
  rules: { id: string; title: string; content: string }[]
  notice: string
}
export default function CustomTemplate({ runId }: { runId: string }) {
  const [candidate, setCandidate] = useState<Candidate | null>(null),
    [files, setFiles] = useState<Record<string, string>>({}),
    [selected, setSelected] = useState<string[]>([]),
    [rules, setRules] = useState<string[]>([]),
    [path, setPath] = useState(''),
    [name, setName] = useState(''),
    [spec, setSpec] = useState(''),
    [previous, setPrevious] = useState(''),
    [versions, setVersions] = useState<{ id: string; version: number; name: string }[]>([]),
    [preview, setPreview] = useState<{
      digest: string
      files: Record<string, string>
      spec: object
      rules: object[]
    } | null>(null),
    [busy, setBusy] = useState(false),
    [error, setError] = useState(''),
    [message, setMessage] = useState('')
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
  function payload() {
    return {
      source_run_id: runId,
      source_digest: candidate?.source_digest,
      name,
      files: Object.fromEntries(selected.map((p) => [p, files[p]])),
      rule_ids: rules,
      previous_id: previous || null,
      spec: JSON.parse(spec),
    }
  }
  const post = (url: string, body: object) =>
    apiJson(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    })
  return (
    <details className="expansion-panel">
      <summary>另存为可复用模板</summary>
      <p>
        仅保存你选择的受控源码、测试与通用规则。先编辑去掉业务隐私，再预览并确认；每次保存为不可变的新版本。
      </p>
      <button
        disabled={busy}
        onClick={() =>
          void act(async () => {
            const c = await apiJson<Candidate>('/custom-templates/projects/' + runId + '/candidate')
            setCandidate(c)
            setFiles(Object.fromEntries(c.files.map((f) => [f.path, f.content])))
            setSelected(c.files.filter((f) => !f.blocked).map((f) => f.path))
            setPath(c.files[0]?.path || '')
            setSpec(JSON.stringify(c.spec, null, 2))
            setName(String(c.spec?.product_name || ''))
            setPreview(null)
            setVersions(
              (
                await apiJson<{ id: string; version: number; name: string; custom?: boolean }[]>(
                  '/templates',
                )
              ).filter((v) => v.custom),
            )
            setMessage('')
          })
        }
      >
        读取候选内容
      </button>
      {candidate && (
        <>
          <p>{candidate.notice}</p>
          <label>
            模板名称
            <input
              value={name}
              maxLength={100}
              onChange={(e) => {
                setName(e.target.value)
                setPreview(null)
              }}
            />
          </label>
          <label>
            保存方式
            <select
              value={previous}
              onChange={(e) => {
                setPrevious(e.target.value)
                setPreview(null)
              }}
            >
              <option value="">创建独立模板</option>
              {versions.map((v) => (
                <option key={v.id} value={v.id.replace('custom-', '')}>
                  接续 {v.name} v{v.version}
                </option>
              ))}
            </select>
          </label>
          {candidate.files.map((f) => (
            <label key={f.path}>
              <input
                type="checkbox"
                checked={selected.includes(f.path)}
                onChange={(e) => {
                  setSelected(
                    e.target.checked ? [...selected, f.path] : selected.filter((v) => v !== f.path),
                  )
                  setPreview(null)
                }}
              />
              {f.path} · {f.bytes} 字节 {f.blocked ? '疑似敏感内容已清空，需手动写成通用内容' : ''}
            </label>
          ))}
          <label>
            检查并编辑源文件
            <select value={path} onChange={(e) => setPath(e.target.value)}>
              {candidate.files.map((f) => (
                <option key={f.path}>{f.path}</option>
              ))}
            </select>
          </label>
          {path && (
            <textarea
              className="source-editor"
              aria-label="模板源文件内容"
              value={files[path] || ''}
              maxLength={65536}
              onChange={(e) => {
                setFiles({ ...files, [path]: e.target.value })
                setPreview(null)
              }}
            />
          )}
          <details>
            <summary>通用产品方案（可编辑 JSON）</summary>
            <textarea
              className="source-editor"
              aria-label="模板通用方案"
              value={spec}
              onChange={(e) => {
                setSpec(e.target.value)
                setPreview(null)
              }}
            />
          </details>
          {candidate.rules.map((r) => (
            <label key={r.id}>
              <input
                type="checkbox"
                checked={rules.includes(r.id)}
                onChange={(e) => {
                  setRules(e.target.checked ? [...rules, r.id] : rules.filter((v) => v !== r.id))
                  setPreview(null)
                }}
              />
              通用规则：{r.title} · {r.content}
            </label>
          ))}
          <button
            disabled={busy || !name.trim()}
            onClick={() =>
              void act(async () => {
                const result = (await post('/custom-templates/preview', payload())) as {
                  bundle: typeof preview
                }
                setPreview(result.bundle)
              })
            }
          >
            预览将保存的全部内容
          </button>
        </>
      )}
      {preview && (
        <div className="clarification-card">
          <b>保存内容预览</b>
          <p>请确认源码与方案不包含私人或业务数据。自动检测不能代替你的核对。</p>
          <details>
            <summary>产品方案与规则</summary>
            <pre>{JSON.stringify({ spec: preview.spec, rules: preview.rules }, null, 2)}</pre>
          </details>
          {Object.entries(preview.files).map(([p, t]) => (
            <details key={p}>
              <summary>{p}</summary>
              <pre>{t}</pre>
            </details>
          ))}
          <button
            disabled={busy}
            onClick={() =>
              void act(async () => {
                const saved = (await post('/custom-templates', {
                  ...payload(),
                  token: preview.digest,
                  confirmed: true,
                })) as { version: number }
                setMessage('已保存模板 v' + saved.version + '，可从能力模板入口选择。')
                setPreview(null)
                window.dispatchEvent(new Event('templates-changed'))
              })
            }
          >
            我已检查内容，确认保存模板
          </button>
        </div>
      )}
      {message && <p>{message}</p>}
      {error && <p className="inline-state error">{error}</p>}
    </details>
  )
}
