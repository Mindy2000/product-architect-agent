import { API_BASE, apiFetch } from './api'
import { useEffect, useState } from 'react'
const API = API_BASE
type FileItem = { id: string; name: string; bytes: number; status: string; created_at: number }
type Stats = {
  file_bytes: number
  file_count: number
  pending_count: number
  limits: { total_file_bytes: number; file_bytes: number }
}
type BackupPreview = {
  token: string
  attachment_count: number
  uncompressed_bytes: number
  schema: { version: number }
  manifest: { source_run_id: string; created_at: number }
}
async function data(response: Response) {
  const value = await response.json()
  if (!response.ok)
    throw new Error(
      typeof value.detail === 'string' ? value.detail : '操作未完成，请检查文件或稍后重试。',
    )
  return value
}
const mib = (bytes: number) => (bytes / 1024 / 1024).toFixed(1) + ' MiB'
export default function ApplicationStorage({
  runId,
  onRestored,
}: {
  runId: string
  onRestored?: (id: string) => void
}) {
  const [items, setItems] = useState<FileItem[]>([]),
    [stats, setStats] = useState<Stats | null>(null),
    [offset, setOffset] = useState(0),
    [total, setTotal] = useState(0),
    [busy, setBusy] = useState(false),
    [error, setError] = useState(''),
    [notice, setNotice] = useState(''),
    [download, setDownload] = useState(''),
    [preview, setPreview] = useState<BackupPreview | null>(null),
    [cleanup, setCleanup] = useState<{ items: unknown[] } | null>(null)
  async function load() {
    const [listed, usage] = await Promise.all([
      apiFetch(`${API}/projects/${runId}/files?offset=${offset}&limit=20`).then(data),
      apiFetch(`${API}/projects/${runId}/storage`).then(data),
    ])
    setItems(listed.items)
    setTotal(listed.total)
    setStats(usage)
  }
  useEffect(() => {
    let stop = false
    void Promise.all([
      apiFetch(`${API}/projects/${runId}/files?offset=${offset}&limit=20`).then(data),
      apiFetch(`${API}/projects/${runId}/storage`).then(data),
    ])
      .then(([listed, usage]) => {
        if (!stop) {
          setItems(listed.items)
          setTotal(listed.total)
          setStats(usage)
        }
      })
      .catch((e) => {
        if (!stop) setError(e instanceof Error ? e.message : '读取文件失败')
      })
    return () => {
      stop = true
    }
  }, [runId, offset])
  useEffect(() => {
    setPreview(null)
    setDownload('')
    setCleanup(null)
    setNotice('')
    setError('')
    setOffset(0)
  }, [runId])
  async function act(work: () => Promise<void>) {
    setBusy(true)
    setError('')
    setNotice('')
    try {
      await work()
    } catch (e) {
      setError(e instanceof Error ? e.message : '操作未完成')
    } finally {
      setBusy(false)
    }
  }
  async function upload(files: File[]) {
    await act(async () => {
      for (const file of files) {
        if (file.size > 20 * 1024 * 1024) throw new Error(`${file.name} 超过20MiB`)
        await data(
          await apiFetch(
            `${API}/projects/${runId}/files?filename=${encodeURIComponent(file.name)}`,
            { method: 'POST', body: file },
          ),
        )
      }
      await load()
      setNotice('文件已保存在本应用，不会自动发送给模型。')
    })
  }
  async function remove(file: FileItem) {
    if (!window.confirm(`删除“${file.name}”？引用此文件的业务记录不会自动修改。`)) return
    await act(async () => {
      await data(await apiFetch(`${API}/projects/${runId}/files/${file.id}`, { method: 'DELETE' }))
      await load()
    })
  }
  async function exportData() {
    await act(async () => {
      setDownload('')
      const value = await data(
        await apiFetch(`${API}/projects/${runId}/data-backups`, { method: 'POST' }),
      )
      setDownload('http://127.0.0.1:8000' + value.download_url)
      setNotice('已生成数据库与文件备份，可点击下载。备份不包含源码或API配置。')
    })
  }
  async function inspect(file: File) {
    await act(async () => {
      setPreview(null)
      if (file.size > 512 * 1024 * 1024) throw new Error('数据备份不能超过512MiB')
      setPreview(
        await data(
          await apiFetch(API + '/application-data/preview', { method: 'POST', body: file }),
        ),
      )
    })
  }
  async function restore() {
    if (
      !preview ||
      !window.confirm(
        '将使用当前项目源码，把备份数据库与文件恢复为独立暂停项目。原项目数据保留，当前不启动应用、不调用模型。继续吗？',
      )
    )
      return
    await act(async () => {
      const value = await data(
        await apiFetch(API + '/application-data/restore', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ token: preview.token, source_run_id: runId }),
        }),
      )
      setPreview(null)
      setNotice('已创建独立暂停副本 ' + value.run_id + '，原数据保留。')
      onRestored?.(value.run_id)
    })
  }
  async function cleanupFiles() {
    if (
      !cleanup ||
      !window.confirm(
        `整理预览中的 ${cleanup.items.length} 项过期未完成上传/临时文件？不包含已完成文件。`,
      )
    )
      return
    await act(async () => {
      await data(await apiFetch(`${API}/projects/${runId}/storage/cleanup`, { method: 'POST' }))
      setCleanup(null)
      await load()
      setNotice('已整理过期临时文件。')
    })
  }
  return (
    <details className="project-maintenance">
      <summary>本地文件与业务数据备份</summary>
      <p>
        无需登录，文件仅供本机应用使用。上传、删除、备份或恢复操作会关闭当前项目预览；完成后可重新打开。
      </p>
      {stats && (
        <p>
          文件 {stats.file_count} 份 · {mib(stats.file_bytes)} /{' '}
          {mib(stats.limits.total_file_bytes)} · 未完成上传 {stats.pending_count} 份
        </p>
      )}
      <fieldset disabled={busy}>
        <legend>文件管理</legend>
        <label>
          上传图片 / PDF / TXT / Markdown（单份20MiB）
          <input
            type="file"
            accept=".png,.jpg,.jpeg,.pdf,.txt,.md,.markdown"
            multiple
            onChange={(e) => {
              const files = Array.from(e.target.files ?? [])
              e.target.value = ''
              if (files.length) void upload(files)
            }}
          />
        </label>
        <p>只保存和下载，不自动OCR、建知识库或发给模型。</p>
        {items.map((file) => (
          <article key={file.id}>
            <strong>{file.name}</strong>
            <small>
              {' '}
              · {mib(file.bytes)} · {file.status === 'ready' ? '已完成' : '上传未完成'}
            </small>
            {file.status === 'ready' && (
              <div>
                <a href={`${API}/projects/${runId}/files/${file.id}/download`}>下载</a>
                <button type="button" onClick={() => void remove(file)}>
                  删除
                </button>
              </div>
            )}
          </article>
        ))}
        <button
          type="button"
          disabled={offset === 0}
          onClick={() => setOffset((value) => Math.max(0, value - 20))}
        >
          上一批
        </button>
        <button
          type="button"
          disabled={offset + 20 >= total}
          onClick={() => setOffset((value) => value + 20)}
        >
          下一批
        </button>
        <button type="button" onClick={() => void act(load)}>
          刷新文件
        </button>
        <button
          type="button"
          onClick={() =>
            void act(async () =>
              setCleanup(await data(await apiFetch(`${API}/projects/${runId}/storage/cleanup`))),
            )
          }
        >
          预览可整理的临时文件
        </button>
        {cleanup && (
          <p>
            24小时以上临时项目 {cleanup.items.length} 项{' '}
            <button
              type="button"
              disabled={!cleanup.items.length}
              onClick={() => void cleanupFiles()}
            >
              确认整理
            </button>
          </p>
        )}
      </fieldset>
      <fieldset disabled={busy}>
        <legend>数据库与文件备份</legend>
        <p>
          备份包含业务数据和上传文件，请妥善保存。源码仍通过原“交付与版本”导出；两种备份用途不同。
        </p>
        <button type="button" onClick={() => void exportData()}>
          生成本地数据备份
        </button>
        {download && <a href={download}>下载数据备份 ZIP</a>}
        <label>
          选择数据备份进行预览
          <input
            type="file"
            accept=".zip"
            onChange={(e) => {
              const file = e.target.files?.[0]
              e.target.value = ''
              if (file) void inspect(file)
            }}
          />
        </label>
        {preview && (
          <div>
            <p>
              数据结构版本 {preview.schema.version} · 附件 {preview.attachment_count} 份 · 展开大小{' '}
              {mib(preview.uncompressed_bytes)}
            </p>
            <p>
              文件清单与哈希已核对；恢复时会进一步检查数据库及附件索引。当前源码的数据结构必须匹配，恢复后生成独立暂停副本。
            </p>
            <button type="button" onClick={() => void restore()}>
              确认恢复为独立副本
            </button>
          </div>
        )}
        <button
          type="button"
          onClick={() =>
            void act(async () => {
              const plan = await data(await apiFetch(API + '/application-data/cleanup'))
              if (!plan.items.length) {
                setNotice('没有过期的数据备份预览。')
                return
              }
              if (
                !window.confirm(
                  `删除 ${plan.items.length} 份超过24小时的备份预览暂存？已导出的备份和项目数据保留。`,
                )
              )
                return
              await data(await apiFetch(API + '/application-data/cleanup', { method: 'POST' }))
              setNotice('过期预览暂存已整理。')
            })
          }
        >
          整理过期备份预览
        </button>
      </fieldset>
      {busy && <p role="status">正在处理本地文件或数据，请勿重复提交…</p>}
      {error && <p role="alert">{error}</p>}
      {notice && <p role="status">{notice}</p>}
    </details>
  )
}
