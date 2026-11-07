import { API_BASE, apiFetch } from './api'
import { useState } from 'react'
const API = API_BASE
type Evidence = {
  retrieval_id?: string
  provider_id?: string
  query?: string
  chunks: {
    chunk_id: string
    document_id?: string
    document_sha256?: string
    content: string
    source: { title: string; location?: string }
  }[]
}
export default function KnowledgeEvidence({ kind, id }: { kind: 'designs' | 'runs'; id: string }) {
  const [citationStates, setCitationStates] = useState<Record<string, string>>({})
  const [evidence, setEvidence] = useState<Evidence | null>(null),
    [error, setError] = useState('')
  async function load() {
    setError('')
    try {
      const response = await apiFetch(`${API}/${kind}/${id}/knowledge-evidence`)
      if (!response.ok) throw new Error('无法读取已保存的知识库引用')
      setEvidence(await response.json())
    } catch (e) {
      setError(e instanceof Error ? e.message : '读取失败')
    }
  }
  async function check() {
    if (!evidence) return
    try {
      const response = await apiFetch(API + '/library/citations/check', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          chunks: evidence.chunks.map(({ content, ...metadata }) => metadata),
        }),
      })
      if (!response.ok) throw new Error('引用核对失败')
      const result = await response.json()
      setCitationStates(
        Object.fromEntries(
          result.items.map((item: { chunk_id: string; status: string }) => [
            item.chunk_id,
            item.status,
          ]),
        ),
      )
    } catch (e) {
      setError(e instanceof Error ? e.message : '核对失败')
    }
  }
  const labels: Record<string, string> = {
    ready: '来源仍有效',
    superseded: '有新版本，当前为历史引用',
    deleted: '来源已删除，冻结原文保留',
    changed: '来源内容已变化',
    not_ready: '来源暂未就绪',
    remote_snapshot: 'Dify冻结引用；未另发请求核对',
  }
  return (
    <details
      className="knowledge-evidence"
      onToggle={(e) => {
        if (e.currentTarget.open && !evidence) void load()
      }}
    >
      <summary>查看本次保存的知识库引用原文</summary>
      {error && <p role="alert">{error}</p>}
      {evidence && (
        <>
          <small>
            检索方式：
            {evidence.provider_id === 'local_full_text'
              ? '本地全文检索'
              : evidence.provider_id === 'dify_cloud'
                ? 'Dify远程语义检索'
                : (evidence.provider_id ?? '未使用')}{' '}
            · {evidence.retrieval_id?.slice(0, 8)}
          </small>
          <button type="button" onClick={() => void check()}>
            核对来源当前状态
          </button>
          <p>检索问题：{evidence.query ?? '无'}</p>
          {!evidence.chunks.length && <p>本次没有检索命中，不应声称已引用资料。</p>}
          {evidence.chunks.map((item) => (
            <article key={item.chunk_id}>
              <b>
                {item.source.title} · {item.source.location}
              </b>
              <small>{labels[citationStates[item.chunk_id]] ?? '尚未核对当前来源'}</small>
              <pre>{item.content}</pre>
            </article>
          ))}
        </>
      )}
    </details>
  )
}
