import { API_BASE, apiFetch } from './api'
import { useState } from 'react'
type Candidate = { run_id: string; commit: string; file_count: number; name: string }
export default function BackupRecovery() {
  const [token, setToken] = useState(''),
    [items, setItems] = useState<Candidate[]>([]),
    [selected, setSelected] = useState(''),
    [warnings, setWarnings] = useState<string[]>([]),
    [error, setError] = useState(''),
    [busy, setBusy] = useState(false),
    [notice, setNotice] = useState('')
  async function preview(file: File) {
    setBusy(true)
    setError('')
    setToken('')
    setItems([])
    try {
      if (file.size > 64 * 1024 * 1024) throw new Error('备份超过64MiB。')
      const r = await apiFetch(API_BASE + '/recovery/preview', { method: 'POST', body: file })
      const data = await r.json()
      if (!r.ok) throw new Error(data.detail)
      setToken(data.token)
      setItems(data.candidates)
      setSelected(
        data.candidates[0] ? data.candidates[0].run_id + ':' + data.candidates[0].commit : '',
      )
      setWarnings(data.warnings ?? [])
    } catch (e) {
      setError(e instanceof Error ? e.message : '预览失败')
    } finally {
      setBusy(false)
    }
  }
  async function restore() {
    const item = items.find((item) => item.run_id + ':' + item.commit === selected)
    if (
      !item ||
      !window.confirm(
        '将复制此快照为独立项目，并保持暂停。原项目保留，当前不会运行构建或调用模型。继续吗？',
      )
    )
      return
    setBusy(true)
    setError('')
    try {
      const r = await apiFetch(API_BASE + '/recovery/restore', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ token, run_id: item.run_id, commit: item.commit }),
      })
      const data = await r.json()
      if (!r.ok) throw new Error(data.detail)
      setNotice(
        '已恢复为暂停项目 ' + data.run_id + '。刷新项目记录后打开，可显式恢复执行构建验收。',
      )
    } catch (e) {
      setError(e instanceof Error ? e.message : '恢复失败')
    } finally {
      setBusy(false)
    }
  }
  return (
    <details className="project-maintenance">
      <summary>从项目备份恢复</summary>
      <p>
        先校验ZIP清单和源码，再选择快照恢复。仅复制源码与方案；运行数据库、密钥和知识库不包含在备份中。预览暂存24小时。
      </p>
      <input
        aria-label="选择项目备份ZIP"
        type="file"
        accept=".zip"
        disabled={busy}
        onChange={(e) => {
          const file = e.target.files?.[0]
          if (file) void preview(file)
          e.target.value = ''
        }}
      />
      {warnings.map((warning, i) => (
        <p key={i}>{warning}</p>
      ))}
      {items.length > 0 && (
        <>
          <select
            aria-label="选择恢复快照"
            value={selected}
            disabled={busy}
            onChange={(e) => setSelected(e.target.value)}
          >
            {items.map((item) => (
              <option key={item.run_id + item.commit} value={item.run_id + ':' + item.commit}>
                {item.name} · {item.run_id.slice(0, 8)} · {item.commit.slice(0, 8)} ·{' '}
                {item.file_count} 文件
              </option>
            ))}
          </select>
          <button disabled={busy || !token} onClick={() => void restore()}>
            恢复为独立暂停项目
          </button>
        </>
      )}
      {busy && <p>正在处理…</p>}
      {error && <p role="alert">{error}</p>}
      {notice && <p role="status">{notice}</p>}
    </details>
  )
}
