import { API_BASE, apiFetch } from './api'
import { useEffect, useRef, useState } from 'react'
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
  const current = useRef('')
  current.current = kind + '/' + id
  const [citationStates, setCitationStates] = useState<Record<string, string>>({})
  const [opened, setOpened] = useState(false)
  const [evidence, setEvidence] = useState<Evidence | null>(null),
    [error, setError] = useState('')
  useEffect(() => {
    setEvidence(null)
    setCitationStates({})
    setError('')
    if (opened) void load()
  }, [kind, id, opened])
  async function load() {
    setError('')
    const key = current.current
    try {
      const response = await apiFetch(`${API}/${kind}/${id}/knowledge-evidence`)
      if (!response.ok) throw new Error('无法读取已保存的知识库引用')
      const value = await response.json()
      if (current.current === key) setEvidence(value)
    } catch (e) {
      if (current.current === key) setError(e instanceof Error ? e.message : '读取失败')
    }
  }
  async function check() {
    if (!evidence) return
    const key = current.current
    setError('')
    setCitationStates({})
    try {
      const response = await apiFetch(API + '/library/citations/check', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          chunks: evidence.chunks.map((item) => ({ ...item, provider_id: evidence.provider_id })),
        }),
      })
      if (!response.ok) throw new Error('引用核对失败')
      const result = await response.json()
      if (current.current !== key) return
      setCitationStates(
        Object.fromEntries(
          result.items.map((item: { chunk_id: string; status: string }) => [
            item.chunk_id,
            item.status,
          ]),
        ),
      )
    } catch (e) {
      if (current.current === key) setError(e instanceof Error ? e.message : '核对失败')
    }
  }
  const labels: Record<string, string> = {
    ready: '当前文档与片段仍有效（不代表语义相关性通过）',
    superseded: '有新版本，当前为历史引用',
    deleted: '来源已删除，冻结原文保留',
    changed: '来源或引用正文已变化，需重新核对',
    chunk_missing: '片段不在当前索引中；冻结原文保留',
    invalid_metadata: '引用元信息不完整，无法核对当前来源',
    not_ready: '来源暂未就绪',
    remote_snapshot: 'Dify冻结引用；未另发请求核对',
  }
  return (
    <details
      className="knowledge-evidence"
      onToggle={(e) => {
        setOpened(e.currentTarget.open)
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
