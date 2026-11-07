import { API_BASE, apiFetch, readJson } from './api'
import { useEffect, useState } from 'react'
import VisionPanel from './VisionPanel'
const API = API_BASE
type Policy = { image: string; packages: { name: string; version: string; role: string }[] }
type RequestItem = {
  id: string
  name: string
  version: string
  locked_version: string
  reason: string
  status: string
}
async function payload<T>(response: Response): Promise<T> {
  return readJson<T>(response, '无法读取配置')
}
export default function EngineeringPanel({ runId }: { runId?: string }) {
  const [policy, setPolicy] = useState<Policy | null>(null),
    [requests, setRequests] = useState<RequestItem[]>([]),
    [error, setError] = useState(''),
    [notice, setNotice] = useState(''),
    [name, setName] = useState(''),
    [version, setVersion] = useState(''),
    [reason, setReason] = useState(''),
    [busy, setBusy] = useState(false)
  useEffect(() => {
    let stopped = false
    void apiFetch(API + '/dependencies/policy')
      .then(payload<Policy>)
      .then((value) => {
        if (!stopped) setPolicy(value)
      })
      .catch((e) => {
        if (!stopped) setError(e instanceof Error ? e.message : '配置不可用')
      })
    if (runId)
      void apiFetch(API + '/dependencies/projects/' + runId)
        .then(payload<{ items: RequestItem[] }>)
        .then((value) => {
          if (!stopped) setRequests(value.items)
        })
        .catch((e) => {
          if (!stopped) setError(e instanceof Error ? e.message : '无法读取申请')
        })
    return () => {
      stopped = true
    }
  }, [runId])
  async function submit() {
    if (!runId) return
    setBusy(true)
    setError('')
    setNotice('')
    try {
      const value = await payload<{ message: string }>(
        await apiFetch(API + '/dependencies/projects/' + runId, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ name, version, reason }),
        }),
      )
      setNotice(value.message)
      const listed = await payload<{ items: RequestItem[] }>(
        await apiFetch(API + '/dependencies/projects/' + runId),
      )
      setRequests(listed.items)
    } catch (e) {
      setError(e instanceof Error ? e.message : '申请未保存')
    } finally {
      setBusy(false)
    }
  }
  return (
    <details className="project-maintenance">
      <summary>模型能力与受控依赖</summary>
      <p>
        文字：沿用 DeepSeek
        两个已有选项。视觉理解：已加入阿里云千问适配，按量计费，仅显式授权调用。图片生成：尚未接入。不会自动切换供应商。
      </p>
      <VisionPanel />
      <p>
        依赖仅复用固定镜像白名单；其他包或版本登记审查申请，不自动联网安装。生成代码运行时仍禁网。
      </p>
      {policy && (
        <>
          <small>执行镜像：{policy.image}</small>
          <details>
            <summary>已锁定的依赖版本</summary>
            <ul>
              {policy.packages.map((item) => (
                <li key={item.name}>
                  {item.name} · {item.version} ·{' '}
                  {item.role === 'application'
                    ? '前端依赖'
                    : item.role === 'test'
                      ? '测试依赖'
                      : '构建工具'}
                </li>
              ))}
            </ul>
          </details>
        </>
      )}
      {runId && (
        <>
          <fieldset disabled={busy}>
            <legend>申请项目依赖</legend>
            <label>
              包名
              <input
                value={name}
                maxLength={120}
                placeholder="例如 lucide-react"
                onChange={(e) => setName(e.target.value)}
              />
            </label>
            <label>
              版本（留空表示使用白名单锁定版本）
              <input value={version} maxLength={60} onChange={(e) => setVersion(e.target.value)} />
            </label>
            <label>
              用途
              <textarea
                value={reason}
                maxLength={600}
                onChange={(e) => setReason(e.target.value)}
              />
            </label>
            <button
              type="button"
              disabled={!name.trim() || !reason.trim()}
              onClick={() => void submit()}
            >
              检查并登记申请
            </button>
          </fieldset>
          {requests.map((item) => (
            <p key={item.id}>
              {item.name} {item.version || item.locked_version} ·{' '}
              {item.status === 'available_locked' ? '可复用固定版本' : '等待依赖与兼容性审查'} ·{' '}
              {item.reason}
            </p>
          ))}
        </>
      )}
      {notice && <p role="status">{notice}</p>}
      {error && <p role="alert">{error}</p>}
    </details>
  )
}
