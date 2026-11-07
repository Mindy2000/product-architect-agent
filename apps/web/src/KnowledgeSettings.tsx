import { API_BASE, apiFetch } from './api'
import { useEffect, useState } from 'react'
const API = API_BASE
type Configuration = {
  revision: number
  provider_id: 'disabled' | 'external_pending' | 'local_full_text' | 'dify_cloud'
  enabled: boolean
  endpoint: string
  collection_ids: string[]
  credential_env: string
  timeout_seconds: number
}
export default function KnowledgeSettings() {
  const [config, setConfig] = useState<Configuration | null>(null),
    [collections, setCollections] = useState(''),
    [error, setError] = useState(''),
    [notice, setNotice] = useState(''),
    [busy, setBusy] = useState(false)
  async function load() {
    try {
      const response = await apiFetch(API + '/knowledge/config')
      if (!response.ok) throw new Error('无法读取RAG接口配置')
      const value = await response.json()
      setConfig(value)
      setCollections(value.collection_ids.join('\n'))
      setError('')
    } catch (e) {
      setError(e instanceof Error ? e.message : '读取失败')
    }
  }
  useEffect(() => {
    void load()
  }, [])
  async function save() {
    if (!config) return
    setBusy(true)
    setError('')
    setNotice('')
    try {
      const response = await apiFetch(API + '/knowledge/config', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          ...config,
          enabled: ['local_full_text', 'dify_cloud'].includes(config.provider_id) && config.enabled,
          collection_ids: collections
            .split('\n')
            .map((item) => item.trim())
            .filter(Boolean),
        }),
      })
      const value = await response.json()
      if (!response.ok)
        throw new Error(
          typeof value.detail === 'string'
            ? value.detail
            : '配置格式不符合要求，请检查服务地址、环境变量名称与数量。',
        )
      setConfig(value)
      setNotice('接口配置已保存；保存不触发检索；Dify显式选择的知识库可独立使用。')
    } catch (e) {
      setError(e instanceof Error ? e.message : '保存失败')
    } finally {
      setBusy(false)
    }
  }
  return (
    <details className="knowledge-settings card">
      <summary>知识检索接口设置</summary>
      <p>本地关键词检索和Dify语义检索可选择；未选择远程库时不发送需求。</p>
      {config && (
        <fieldset disabled={busy}>
          <label>
            接入状态
            <select
              value={config.provider_id}
              onChange={(e) =>
                setConfig({
                  ...config,
                  provider_id: e.target.value as Configuration['provider_id'],
                })
              }
            >
              <option value="disabled">关闭统一检索接口</option>
              <option value="local_full_text">本地全文检索</option>
              <option value="dify_cloud">Dify Cloud（免费额度保护）</option>
              <option value="external_pending">外部RAG（等待选择与适配）</option>
            </select>
          </label>
          {['local_full_text', 'dify_cloud'].includes(config.provider_id) && (
            <label>
              启用统一接口
              <input
                type="checkbox"
                checked={config.enabled}
                onChange={(e) => setConfig({ ...config, enabled: e.target.checked })}
              />
              <small>工作台选中的项目知识库可直接使用，不依赖此默认设置。</small>
            </label>
          )}
          <label>
            服务地址
            <input
              type="url"
              value={config.endpoint}
              placeholder="Dify填写https://api.dify.ai/v1"
              onChange={(e) => setConfig({ ...config, endpoint: e.target.value })}
            />
          </label>
          <label>
            知识库标识（每行一个，可暂不填写）
            <textarea value={collections} onChange={(e) => setCollections(e.target.value)} />
          </label>
          <label>
            密钥所在环境变量的名称
            <input
              value={config.credential_env}
              onChange={(e) => setConfig({ ...config, credential_env: e.target.value })}
            />
            <small>这里只填名称，例如RAG_API_KEY；不填写密钥本身。</small>
          </label>
          <label>
            预留请求超时（秒）
            <input
              type="number"
              min={1}
              max={60}
              value={config.timeout_seconds}
              onChange={(e) => setConfig({ ...config, timeout_seconds: Number(e.target.value) })}
            />
          </label>
          <button type="button" disabled={busy} onClick={() => void save()}>
            保存接口配置
          </button>
        </fieldset>
      )}
      <button className="text-button" disabled={busy} onClick={() => void load()}>
        重新读取配置
      </button>
      {notice && <p role="status">{notice}</p>}
      {error && (
        <p className="inline-state error" role="alert">
          {error}
        </p>
      )}
    </details>
  )
}
