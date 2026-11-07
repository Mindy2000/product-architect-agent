import { API_BASE, apiFetch } from './api'
import { useState } from 'react'
import { publicHttpsUrl } from './safeUrl'
import type { WebSource } from './PublicResearch'
type Item = WebSource & { content: string }
export default function WebEvidence({ kind, id }: { kind: 'designs' | 'runs'; id: string }) {
  const [items, setItems] = useState<Item[] | null>(null),
    [error, setError] = useState('')
  async function load() {
    setError('')
    try {
      const response = await apiFetch(`${API_BASE}/${kind}/${id}/web-evidence`)
      const data = await response.json()
      if (!response.ok) throw new Error('暂时无法读取网页引用')
      setItems(data.items)
    } catch (e) {
      setError(e instanceof Error ? e.message : '读取失败')
    }
  }
  return (
    <details className="project-maintenance">
      <summary>公开网页来源与冻结原文</summary>
      <button type="button" onClick={() => void load()}>
        查看本地引用快照（不联网）
      </button>
      {items?.map((item) => (
        <article key={item.id}>
          <strong>{item.title}</strong>
          <p>
            <a href={publicHttpsUrl(item.url)} target="_blank" rel="noopener noreferrer">
              打开原网页
            </a>{' '}
            · {new Date(item.at * 1000).toLocaleString('zh-CN')}
          </p>
          <small>
            快照SHA256：{item.sha256} · 已保留 {item.content.length} 字符
            {item.omitted_characters ? ' · 省略 ' + item.omitted_characters + ' 字符' : ''}
          </small>
          <pre style={{ whiteSpace: 'pre-wrap', maxHeight: 320, overflow: 'auto' }}>
            {item.content}
          </pre>
        </article>
      ))}
      {items?.length === 0 && <p>本任务没有冻结的公开网页来源。</p>}
      {error && <p role="alert">{error}</p>}
    </details>
  )
}
