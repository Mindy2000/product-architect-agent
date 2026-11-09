import { API_BASE, apiFetch } from './api'
import { useEffect, useState } from 'react'
import Versions from './Versions'
import AcceptanceEvidence from './AcceptanceEvidence'
import ProjectImpact from './ProjectImpact'
import ProjectMaintenance from './ProjectMaintenance'

const API = API_BASE
type Item = {
  run_id: string
  parent_run_id?: string
  created_at?: string
  status: string
  title?: string
  operator_assisted: boolean
  build_status?: string
  test_status?: string
  depth?: number
  artifacts: Record<string, boolean>
}
type Lineage = { root_run_id: string; items: Item[]; warnings?: string[] }
const labels: Record<string, string> = {
  source_zip: '源码 ZIP',
  product_spec: '产品方案 JSON',
  report: '交付报告',
  trace: '运行 Trace',
  git_diff: 'Git diff',
  manifest: '文件清单',
  preview: '页面预览',
}
const statusText: Record<string, string> = {
  completed: '执行完成',
  failed_validation: '交付完成，技术检查未通过',
  failed: '运行失败',
  failed_model: '模型调用失败',
  failed_sandbox: '执行环境失败',
  blocked_budget: '预算不足',
  blocked_configuration: '配置待完成',
  interrupted: '服务中断',
  paused: '已暂停',
  cancelled: '已取消',
}
export default function DeliveryCenter({ runId }: { runId: string }) {
  const [data, setData] = useState<Lineage | null>(null),
    [error, setError] = useState(''),
    [loading, setLoading] = useState(true),
    [revision, setRevision] = useState(0)
  const [backingUp, setBackingUp] = useState(false)
  useEffect(() => {
    const controller = new AbortController()
    setLoading(true)
    setError('')
    void apiFetch(`${API}/projects/${runId}/delivery`, { signal: controller.signal })
      .then(async (r) => {
        if (!r.ok) throw new Error('交付与版本信息暂时无法读取。')
        const d = (await r.json()) as Lineage
        if (!controller.signal.aborted) setData(d)
      })
      .catch((e) => {
        if (!controller.signal.aborted) setError(e instanceof Error ? e.message : '读取失败。')
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false)
      })
    return () => controller.abort()
  }, [runId, revision])
  async function downloadBackup() {
    setBackingUp(true)
    setError('')
    try {
      const response = await apiFetch(`${API}/projects/${runId}/backup`, { method: 'POST' })
      if (!response.ok) {
        const body = await response.json().catch(() => null)
        throw new Error(typeof body?.detail === 'string' ? body.detail : '创建备份失败。')
      }
      const blob = await response.blob()
      const url = URL.createObjectURL(blob)
      const link = document.createElement('a')
      link.href = url
      link.download = `product-architect-${runId}-backup.zip`
      link.click()
      URL.revokeObjectURL(url)
    } catch (e) {
      setError(e instanceof Error ? e.message : '创建备份失败。')
    } finally {
      setBackingUp(false)
    }
  }
  const canBackup =
    !!data?.items.length &&
    data.items.every((item) =>
      [
        'completed',
        'failed_validation',
        'failed',
        'failed_model',
        'failed_sandbox',
        'blocked_budget',
        'blocked_configuration',
        'interrupted',
        'paused',
        'cancelled',
      ].includes(item.status),
    )
  return (
    <details className="delivery-center" open>
      <summary>交付与版本管理</summary>
      <p>
        按项目版本查看交付资料与父子关系。恢复旧版本会创建独立的暂停副本，保留已有项目；手动继续后才会重新构建和验收。
      </p>
      <button
        className="text-button"
        disabled={loading || backingUp || !canBackup}
        onClick={() => void downloadBackup()}
      >
        {backingUp ? '正在打包…' : '下载项目备份 ZIP'}
      </button>
      <button className="text-button" disabled={loading} onClick={() => setRevision((x) => x + 1)}>
        {loading ? '读取中…' : '刷新版本记录'}
      </button>
      {!canBackup && !loading && <small>所有关联任务结束后可创建备份。</small>}
      {error && (
        <p className="inline-state error" role="alert">
          {error}
        </p>
      )}
      {data?.warnings?.map((warning) => (
        <p role="status" key={warning}>
          {warning}
        </p>
      ))}
      {data?.items.map((item, index) => (
        <article
          className="delivery-version"
          key={item.run_id}
          style={{ marginLeft: Math.min(item.depth ?? 0, 6) * 8 }}
        >
          <header>
            <div>
              <b>{item.title || '项目版本'}</b>
              <small>
                {index === 0
                  ? '项目起点'
                  : item.parent_run_id
                    ? '基于 ' + item.parent_run_id.slice(0, 8) + ' 创建'
                    : '关联版本'}{' '}
                · {item.run_id.slice(0, 8)}
                {item.created_at ? ' · ' + new Date(item.created_at).toLocaleString('zh-CN') : ''}
              </small>
            </div>
            <span>{statusText[item.status] ?? item.status}</span>
          </header>
          <div className="delivery-checks">
            <span>
              构建：
              {item.build_status === 'passed'
                ? '通过'
                : item.build_status === 'unverified'
                  ? '待重新核对'
                  : item.build_status === 'failed'
                    ? '失败'
                    : '无记录'}
            </span>
            <span>
              浏览器测试：
              {item.test_status === 'passed'
                ? '通过'
                : item.test_status === 'unverified'
                  ? '待重新核对'
                  : item.test_status === 'failed'
                    ? '失败'
                    : item.test_status === 'skipped'
                      ? '已跳过'
                      : '无记录'}
            </span>
            {item.operator_assisted && <span>Codex 辅助完成</span>}
          </div>
          <div className="artifact-links">
            {Object.entries(item.artifacts)
              .filter(([, ready]) => ready)
              .map(([kind]) => (
                <a
                  key={kind}
                  href={
                    kind === 'source_zip'
                      ? `${API}/runs/${item.run_id}/download`
                      : `${API}/runs/${item.run_id}/artifact/${kind}`
                  }
                  download
                >
                  {labels[kind] ?? kind}
                </a>
              ))}
            {!Object.values(item.artifacts).some(Boolean) && <span>此版本暂无交付文件</span>}
          </div>
          <div className="delivery-subsections">
            <AcceptanceEvidence key={item.run_id + '-acceptance'} runId={item.run_id} />
            <ProjectMaintenance key={item.run_id} runId={item.run_id} />
            {item.parent_run_id && <ProjectImpact runId={item.run_id} />}
            <Versions runId={item.run_id} onRestored={() => setRevision((x) => x + 1)} />
          </div>
        </article>
      ))}
      {!loading && data?.items.length === 0 && <p>还没有版本记录。</p>}
    </details>
  )
}
