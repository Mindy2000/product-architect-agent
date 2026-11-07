import { API_BASE, apiFetch } from './api'
import { useEffect, useState } from 'react'
const API = API_BASE + '/vision'
type Artifact = { artifact_id: string; mime_type: 'image/png'; sha256: string }
type Result = {
  text: string
  estimated_cost_cny: number | null
  input_tokens: number
  output_tokens: number
  cached: boolean
}
type Status = {
  model: string
  key_configured: boolean
  calls_enabled: boolean
  calls_used: number
  max_calls: number
  allocated_cny_estimate: number
  budget_cny: number
  last_result: Result | null
}
async function value(response: Response) {
  const data = await response.json()
  if (!response.ok)
    throw new Error(typeof data.detail === 'string' ? data.detail : '视觉操作未完成')
  return data
}
export default function VisionPanel() {
  const [status, setStatus] = useState<Status | null>(null),
    [artifact, setArtifact] = useState<Artifact | null>(null),
    [question, setQuestion] = useState('请描述界面的主要布局，并指出图片中可见的排版问题。'),
    [result, setResult] = useState<Result | null>(null),
    [busy, setBusy] = useState(false),
    [error, setError] = useState('')
  async function refresh() {
    const data = await value(await apiFetch(API + '/status'))
    setStatus(data)
    if (data.last_result) setResult(data.last_result)
  }
  useEffect(() => {
    let stopped = false
    void apiFetch(API + '/status')
      .then(value)
      .then((data) => {
        if (!stopped) {
          setStatus(data)
          if (data.last_result) setResult(data.last_result)
        }
      })
      .catch((e) => {
        if (!stopped) setError(e instanceof Error ? e.message : '状态不可用')
      })
    return () => {
      stopped = true
    }
  }, [])
  async function upload(file: File) {
    setBusy(true)
    setError('')
    try {
      if (file.size > 4 * 1024 * 1024) throw new Error('图片不能超过4MiB')
      setArtifact(await value(await apiFetch(API + '/artifacts', { method: 'POST', body: file })))
    } catch (e) {
      setError(e instanceof Error ? e.message : '图片保存失败')
    } finally {
      setBusy(false)
    }
  }
  async function analyze() {
    if (!artifact) return
    setBusy(true)
    setError('')
    try {
      setResult(
        await value(
          await apiFetch(API + '/analyze', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ question, images: [artifact], max_cost_cny: 0.02 }),
          }),
        ),
      )
    } catch (e) {
      setError(e instanceof Error ? e.message : '分析未完成')
    } finally {
      await refresh().catch(() => {})
      setBusy(false)
    }
  }
  return (
    <details className="project-maintenance">
      <summary>千问视觉理解 · 按需调用</summary>
      <p>
        只分析主动提交的图片；上传只保存在本地，点击分析才发往阿里云。按量计费，不自动巡检或重试，不替代功能验收。
      </p>
      {status && (
        <p>
          {status.model} · Key{status.key_configured ? '已填写' : '未填写'} · 本轮已尝试{' '}
          {status.calls_used}/{status.max_calls} 次 · 已结算/预留估算 ¥
          {status.allocated_cny_estimate.toFixed(6)} / ¥{status.budget_cny}。
          {!status.calls_enabled && '当前调用关闭，新增调用需要另行授权。'}
        </p>
      )}
      <fieldset disabled={busy}>
        <legend>提交图片分析</legend>
        <label>
          PNG / JPEG（4MiB以内，最长边缩小到512像素）
          <input
            type="file"
            accept="image/png,image/jpeg"
            onChange={(e) => {
              const file = e.target.files?.[0]
              e.target.value = ''
              if (file) void upload(file)
            }}
          />
        </label>
        {artifact && <small>图片已保存本地，尚未发送。</small>}
        <label>
          分析问题
          <textarea
            value={question}
            maxLength={4000}
            onChange={(e) => setQuestion(e.target.value)}
          />
        </label>
        <button
          type="button"
          disabled={
            !artifact ||
            !question.trim() ||
            !status?.calls_enabled ||
            status.calls_used >= status.max_calls
          }
          onClick={() => void analyze()}
        >
          发送图片并分析（按量计费）
        </button>
        <button
          type="button"
          onClick={() =>
            void refresh().catch((e) => setError(e instanceof Error ? e.message : '读取失败'))
          }
        >
          刷新本地状态
        </button>
      </fieldset>
      {result && (
        <article>
          <p>最近一次结果{result.cached ? '（本地缓存）' : ''}</p>
          <pre style={{ whiteSpace: 'pre-wrap' }}>{result.text}</pre>
          <small>
            输入 {result.input_tokens} / 输出 {result.output_tokens} Token · 估算费用{' '}
            {result.estimated_cost_cny === null
              ? '未知，保留预留'
              : '¥' + result.estimated_cost_cny.toFixed(8)}
            ，实际以平台账单为准。
          </small>
        </article>
      )}
      {error && <p role="alert">{error}</p>}
    </details>
  )
}
