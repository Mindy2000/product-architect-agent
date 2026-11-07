import { API_BASE, apiFetch } from './api'
import { useEffect, useState } from 'react'
const API = API_BASE
type Status = {
  policy_revision: string
  reserved_credits: number
  local_remaining_credits: number
  last_observed_used_credits?: number
  local_credit_ceiling: number
  configured: boolean
  model: string
  free_confirmed_at?: string
  paused?: string
  calls_last_24h: number
  collections: { id: string; name: string }[]
  documents: { dataset_id: string; name: string; status: string }[]
}
type LocalDocument = { id: string; filename: string; status: string }
type Chunk = { chunk_id: string; content: string; source: { title: string; location: string } }
async function read(path: string, method = 'GET', body?: object) {
  const response = await apiFetch(API + path, {
    method,
    headers: body ? { 'Content-Type': 'application/json' } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  })
  const value = await response.json()
  if (!response.ok)
    throw new Error(typeof value.detail === 'string' ? value.detail : '操作未完成，请检查填写内容')
  return value
}
export default function DifyKnowledge({
  selected,
  onChange,
  disabled = false,
}: {
  selected: string[]
  onChange: (ids: string[]) => void
  disabled?: boolean
}) {
  const [status, setStatus] = useState<Status | null>(null),
    [error, setError] = useState(''),
    [notice, setNotice] = useState(''),
    [busy, setBusy] = useState(false)
  const [name, setName] = useState('实验知识库'),
    [dataset, setDataset] = useState(''),
    [locals, setLocals] = useState<{ id: string; name: string }[]>([]),
    [local, setLocal] = useState(''),
    [documents, setDocuments] = useState<LocalDocument[]>([]),
    [document, setDocument] = useState(''),
    [query, setQuery] = useState(''),
    [chunks, setChunks] = useState<Chunk[]>([])
  const [observed, setObserved] = useState(0),
    [ceiling, setCeiling] = useState(60),
    [freeConfirmed, setFreeConfirmed] = useState(false),
    [consoleChecked, setConsoleChecked] = useState(false)
  async function load() {
    const value = (await read('/dify/status')) as Status
    setStatus(value)
    setObserved(value.last_observed_used_credits ?? 0)
    setCeiling(value.local_credit_ceiling)
    setDataset((previous) => previous || value.collections[0]?.id || '')
  }
  useEffect(() => {
    void load().catch((e) => setError(e.message))
    void read('/library/collections')
      .then((value) => setLocals(value.items))
      .catch(() => {})
  }, [])
  useEffect(() => {
    setDocuments([])
    setDocument('')
    if (local)
      void read('/library/collections/' + local + '/documents')
        .then((value) =>
          setDocuments(value.items.filter((item: LocalDocument) => item.status === 'ready')),
        )
        .catch((e) => setError(e.message))
  }, [local])
  async function action(work: () => Promise<void>) {
    setBusy(true)
    setError('')
    setNotice('')
    try {
      await work()
      await load()
    } catch (e) {
      setError(e instanceof Error ? e.message : '操作失败。')
    } finally {
      setBusy(false)
    }
  }
  async function sync() {
    const doc = documents.find((item) => item.id === document)
    if (
      !doc ||
      !window.confirm(
        `将“${doc.filename}”提取的文字发送到Dify Cloud并使用已确认的免费体验额度建立索引？`,
      )
    )
      return
    await action(async () => {
      const value = await read('/dify/collections/' + dataset + '/sync', 'POST', {
        document_id: document,
      })
      setNotice(
        value.reused ? '资料已同步，复用已有索引。' : '已提交远程索引；稍后主动刷新，不自动轮询。',
      )
    })
  }
  const ready = !!status?.configured && !!status.free_confirmed_at && !status.paused
  return (
    <details className="knowledge-library">
      <summary>Dify 远程语义知识库 · 已选 {selected.length}</summary>
      <p>
        可选功能。每分钟最多4次请求、24小时40次请求/12次向量操作，最多4份实验资料；同查询缓存24小时，无自动重试或付费回退。
      </p>
      {status && (
        <>
          <small>
            密钥{status.configured ? '已配置' : '未配置'} · 模型 {status.model} · 请求台账{' '}
            {status.calls_last_24h}/40。最近人工观察已用{' '}
            {status.last_observed_used_credits ?? '未知'}，本地保护线 {status.local_credit_ceiling}
            ，保留额度 {status.reserved_credits}，可用保护空间 {status.local_remaining_credits}
            。这些不是平台实时余额。
          </small>
          <details open={!status.free_confirmed_at}>
            <summary>确认本机免费策略（不联网）</summary>
            <p>
              先在 Dify 控制台核对免费模型、可用额度及禁止付费回退配置。填写 Key
              不代表有免费额度；这里不会验证账户或购买服务。
            </p>
            <fieldset disabled={disabled || busy}>
              <label>
                控制台观察的已用额度
                <input
                  aria-label="Dify观察已用额度"
                  type="number"
                  min={0}
                  max={1000000000}
                  value={observed}
                  onChange={(e) => setObserved(Number(e.target.value))}
                />
              </label>
              <label>
                本地累计保护线（最多60）
                <input
                  aria-label="Dify本地保护线"
                  type="number"
                  min={1}
                  max={60}
                  value={ceiling}
                  onChange={(e) => setCeiling(Number(e.target.value))}
                />
              </label>
              <label>
                <input
                  type="checkbox"
                  checked={freeConfirmed}
                  onChange={(e) => setFreeConfirmed(e.target.checked)}
                />
                我已核对该模型有可用免费额度，且不会回退到付费模型
              </label>
              <button
                disabled={
                  !status.configured ||
                  !freeConfirmed ||
                  !Number.isInteger(observed) ||
                  !Number.isInteger(ceiling) ||
                  observed < 0 ||
                  ceiling < 1 ||
                  ceiling > 60
                }
                onClick={() =>
                  void action(async () => {
                    const value = await read('/dify/policy', 'PUT', {
                      expected_revision: status.policy_revision,
                      observed_used_credits: observed,
                      local_total_credit_ceiling: ceiling,
                      free_model_confirmed: true,
                      no_paid_fallback: true,
                    })
                    setFreeConfirmed(false)
                    setNotice(value.message)
                  })
                }
              >
                保存免费策略（仅本机）
              </button>
            </fieldset>
            <p>
              历史观察量不会降低，原请求及不确定预留不清零，不按月自动重置。保存策略不会解除已有暂停，也不会重发请求。
            </p>
          </details>
          {status.paused && (
            <div role="alert">
              <p>已暂停：{status.paused}</p>
              <label>
                <input
                  type="checkbox"
                  checked={consoleChecked}
                  disabled={busy || disabled}
                  onChange={(e) => setConsoleChecked(e.target.checked)}
                />
                我已核对控制台并处理鉴权、额度或限流问题
              </label>
              <button
                disabled={
                  disabled ||
                  busy ||
                  !consoleChecked ||
                  !status.free_confirmed_at ||
                  status.local_remaining_credits <= 0
                }
                onClick={() =>
                  void action(async () => {
                    const value = await read('/dify/resume', 'POST', {
                      expected_revision: status.policy_revision,
                      console_checked: true,
                    })
                    setConsoleChecked(false)
                    setNotice(value.message)
                  })
                }
              >
                解除本机暂停（不重试请求）
              </button>
            </div>
          )}
          <div className="knowledge-choices">
            {status.collections.map((item) => (
              <label key={item.id}>
                <input
                  type="checkbox"
                  checked={selected.includes(item.id)}
                  disabled={
                    disabled ||
                    busy ||
                    !ready ||
                    (!selected.includes(item.id) && selected.length >= 2)
                  }
                  onChange={(e) =>
                    onChange(
                      e.target.checked
                        ? [...selected, item.id]
                        : selected.filter((id) => id !== item.id),
                    )
                  }
                />
                {item.name}
              </label>
            ))}
          </div>
          <fieldset disabled={disabled || busy || !ready}>
            <label>
              新建实验库名称
              <input maxLength={40} value={name} onChange={(e) => setName(e.target.value)} />
            </label>
            <button
              disabled={!name.trim()}
              onClick={() =>
                void action(async () => {
                  const value = await read('/dify/collections', 'POST', { name })
                  setDataset(value.id)
                })
              }
            >
              创建空知识库
            </button>
            <label>
              管理远程知识库
              <select value={dataset} onChange={(e) => setDataset(e.target.value)}>
                <option value="">选择知识库</option>
                {status.collections.map((item) => (
                  <option key={item.id} value={item.id}>
                    {item.name}
                  </option>
                ))}
              </select>
            </label>
            <label>
              从本地知识库选择资料
              <select value={local} onChange={(e) => setLocal(e.target.value)}>
                <option value="">选择本地库</option>
                {locals.map((item) => (
                  <option key={item.id} value={item.id}>
                    {item.name}
                  </option>
                ))}
              </select>
            </label>
            <label>
              就绪资料
              <select value={document} onChange={(e) => setDocument(e.target.value)}>
                <option value="">选择资料</option>
                {documents.map((item) => (
                  <option key={item.id} value={item.id}>
                    {item.filename}
                  </option>
                ))}
              </select>
            </label>
            <button disabled={!dataset || !document} onClick={() => void sync()}>
              同步所选资料到Dify
            </button>
            <button
              disabled={!dataset}
              onClick={() =>
                void action(async () => {
                  await read('/dify/collections/' + dataset + '/refresh', 'POST')
                  setNotice('已刷新远程文档状态，同一分钟重复请求复用缓存。')
                })
              }
            >
              刷新远程索引状态
            </button>
            {status.documents
              .filter((item) => item.dataset_id === dataset)
              .map((item) => (
                <p key={item.name}>
                  {item.name} · {item.status === 'completed' ? '索引完成' : item.status}
                </p>
              ))}
            <label>
              检索问题
              <input maxLength={2000} value={query} onChange={(e) => setQuery(e.target.value)} />
            </label>
            <button
              disabled={!query.trim() || !dataset}
              onClick={() =>
                void action(async () => {
                  const value = await read('/dify/retrieve', 'POST', {
                    query,
                    collection_ids: [dataset],
                  })
                  setChunks(value.chunks)
                  setNotice(value.message)
                })
              }
            >
              语义检索并查看来源
            </button>
          </fieldset>
          <button
            disabled={busy}
            onClick={() =>
              void action(async () => {
                await load()
              })
            }
          >
            读取本地台账（不调用远程）
          </button>
        </>
      )}
      {chunks.map((chunk) => (
        <article className="knowledge-evidence" key={chunk.chunk_id}>
          <b>
            {chunk.source.title} · {chunk.source.location}
          </b>
          <pre>{chunk.content}</pre>
        </article>
      ))}
      <p>勾选远程库后，生成方案会将需求发给 Dify 检索并冻结来源。查看本地记录不消耗远程额度。</p>
      {notice && <p role="status">{notice}</p>}
      {error && <p role="alert">{error}</p>}
    </details>
  )
}
