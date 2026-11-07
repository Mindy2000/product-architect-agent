import { API_BASE, apiFetch, readJson } from './api'
import { useEffect, useState } from 'react'
const API = API_BASE
export type ReferenceSource = {
  id: string
  title: string
  filename?: string
  bytes: number
  sha256: string
  omitted_characters?: number
}
async function data<T>(response: Response): Promise<T> {
  return readJson<T>(response, '资料操作未完成，请检查文本格式和大小。')
}
export default function ReferenceLibrary({
  selected,
  onChange,
  disabled = false,
  frozen = [],
}: {
  selected: string[]
  onChange: (ids: string[]) => void
  disabled?: boolean
  frozen?: ReferenceSource[]
}) {
  const [items, setItems] = useState<ReferenceSource[]>([]),
    [title, setTitle] = useState(''),
    [content, setContent] = useState(''),
    [error, setError] = useState(''),
    [notice, setNotice] = useState(''),
    [busy, setBusy] = useState(false),
    [preview, setPreview] = useState<{ title: string; content: string } | null>(null)
  async function load() {
    try {
      const result = await data<{ items: ReferenceSource[] }>(await apiFetch(API + '/references'))
      setItems(result.items)
    } catch (e) {
      setError(e instanceof Error ? e.message : '读取资料失败')
    }
  }
  useEffect(() => {
    void load()
  }, [])
  async function add(name: string, text: string, filename?: string) {
    const added = await data<ReferenceSource>(
      await apiFetch(API + '/references', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ title: name, content: text, filename }),
      }),
    )
    return added
  }
  async function paste() {
    setBusy(true)
    setError('')
    try {
      const added = await add(title, content)
      if (selected.length < 12) onChange([...selected, added.id])
      setTitle('')
      setContent('')
      await load()
      setNotice('文本已保存到本机资料库。')
    } catch (e) {
      setError(e instanceof Error ? e.message : '保存失败')
    } finally {
      setBusy(false)
    }
  }
  async function upload(files: FileList | null) {
    if (!files?.length) return
    setBusy(true)
    setError('')
    const addedIds: string[] = []
    try {
      for (const file of Array.from(files)) {
        if (!/\.(txt|md|markdown)$/i.test(file.name)) throw new Error('请选择TXT或Markdown文件。')
        if (file.size > 2 * 1024 * 1024) throw new Error(`${file.name} 超过2MiB。`)
        let text: string
        try {
          text = new TextDecoder('utf-8', { fatal: true }).decode(await file.arrayBuffer())
        } catch {
          throw new Error(`${file.name} 不是有效UTF-8文本，请转换编码后上传。`)
        }
        const added = await add(file.name, text, file.name)
        addedIds.push(added.id)
      }
      setNotice(`已保存 ${addedIds.length} 份资料。`)
    } catch (e) {
      setError(e instanceof Error ? e.message : '上传失败')
    } finally {
      if (addedIds.length) onChange([...selected, ...addedIds].slice(0, 12))
      await load()
      setBusy(false)
    }
  }
  async function remove(item: ReferenceSource) {
    if (!window.confirm(`删除资料“${item.title}”？已保存方案中的资料快照仍会保留。`)) return
    setBusy(true)
    setError('')
    try {
      const response = await apiFetch(API + '/references/' + item.id, { method: 'DELETE' })
      if (!response.ok) await readJson(response)
      onChange(selected.filter((id) => id !== item.id))
      if (preview?.title === item.title) setPreview(null)
      await load()
      setNotice('已从资料库删除；历史方案的快照不受影响。')
    } catch (e) {
      setError(e instanceof Error ? e.message : '删除失败')
    } finally {
      setBusy(false)
    }
  }
  async function view(item: ReferenceSource) {
    try {
      const result = await data<{ title: string; content: string }>(
        await apiFetch(API + '/references/' + item.id),
      )
      setPreview({ title: result.title, content: result.content })
    } catch (e) {
      setError(e instanceof Error ? e.message : '读取失败')
    }
  }
  const missing = frozen.filter(
    (item) => selected.includes(item.id) && !items.some((current) => current.id === item.id),
  )
  return (
    <details className="reference-library">
      <summary>参考资料 · 已选 {selected.length} / 12</summary>
      <p>资料直接作为文字参考使用。本机保存；生成方案或开发时，选中的内容会提供给所选文字模型。</p>
      <div className="reference-actions">
        <label className="upload-reference">
          上传TXT / Markdown
          <input
            type="file"
            accept=".txt,.md,.markdown,text/plain,text/markdown"
            multiple
            disabled={disabled || busy}
            onChange={(e) => {
              void upload(e.target.files)
              e.target.value = ''
            }}
          />
        </label>
        <button type="button" disabled={busy} onClick={() => void load()}>
          刷新资料
        </button>
      </div>
      <div className="reference-list">
        {items.map((item) => (
          <article key={item.id}>
            <label>
              <input
                type="checkbox"
                checked={selected.includes(item.id)}
                disabled={
                  disabled || busy || (!selected.includes(item.id) && selected.length >= 12)
                }
                onChange={(e) =>
                  onChange(
                    e.target.checked
                      ? [...selected, item.id]
                      : selected.filter((id) => id !== item.id),
                  )
                }
              />
              <span>
                {item.title}
                <small>
                  {(item.bytes / 1024).toFixed(1)} KiB · {item.sha256.slice(0, 10)}
                </small>
              </span>
            </label>
            <button type="button" onClick={() => void view(item)}>
              查看
            </button>
            <button type="button" disabled={disabled || busy} onClick={() => void remove(item)}>
              删除
            </button>
          </article>
        ))}
        {missing.map((item) => (
          <article key={item.id}>
            <span>
              {item.title}
              <small>已从资料库移除，当前方案保留已保存的快照。</small>
            </span>
            <button
              type="button"
              disabled={disabled || busy}
              onClick={() => onChange(selected.filter((id) => id !== item.id))}
            >
              取消引用
            </button>
          </article>
        ))}
        {!items.length && !missing.length && <p>还没有参考资料，可上传文件或粘贴文本。</p>}
      </div>
      {preview && (
        <div className="reference-preview">
          <b>{preview.title}</b>
          <button type="button" onClick={() => setPreview(null)}>
            关闭查看
          </button>
          <pre>{preview.content}</pre>
        </div>
      )}
      <fieldset disabled={disabled || busy}>
        <legend>粘贴文本</legend>
        <label>
          资料标题
          <input value={title} maxLength={160} onChange={(e) => setTitle(e.target.value)} />
        </label>
        <label>
          文本内容
          <textarea
            value={content}
            maxLength={2 * 1024 * 1024}
            onChange={(e) => setContent(e.target.value)}
          />
        </label>
        <button
          type="button"
          disabled={!title.trim() || !content.trim() || disabled || busy}
          onClick={() => void paste()}
        >
          保存文本资料
        </button>
      </fieldset>
      <p>
        单份上限2MiB，资料库最多100份、合计64MiB。每次最多选择12份，按选择顺序直接提供前80,000个字符；超出部分会明确标注，不自动做检索。
      </p>
      {frozen.some((item) => (item.omitted_characters ?? 0) > 0) && (
        <p className="reference-warning">
          当前方案有超出上下文上限的资料：
          {frozen
            .filter((item) => (item.omitted_characters ?? 0) > 0)
            .map((item) => `${item.title}（省略${item.omitted_characters}字符）`)
            .join('、')}
          。
        </p>
      )}
      {notice && <p role="status">{notice}</p>}
      {error && (
        <p className="inline-state error" role="alert">
          {error}
        </p>
      )}
    </details>
  )
}
