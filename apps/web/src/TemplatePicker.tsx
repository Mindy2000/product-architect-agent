import { useEffect, useState } from 'react'
import { apiJson } from './api'
type Template = {
  id: string
  version: number
  spec: { product_name: string; summary: string; acceptance_criteria: string[] }
}
export default function TemplatePicker({
  onSelect,
  model,
  budget,
  steps,
  disabled,
}: {
  onSelect: (id: string) => void
  model: string
  budget: number
  steps: number
  disabled: boolean
}) {
  const [items, setItems] = useState<Template[]>([]),
    [busy, setBusy] = useState(false),
    [error, setError] = useState('')
  useEffect(() => {
    let gone = false
    apiJson<Template[]>('/templates')
      .then((x) => {
        if (!gone) setItems(x)
      })
      .catch((e) => {
        if (!gone) setError(e.message)
      })
    return () => {
      gone = true
    }
  }, [])
  useEffect(() => {
    const refresh = () => {
      void apiJson<Template[]>('/templates')
        .then(setItems)
        .catch((e) => setError(e.message))
    }
    window.addEventListener('templates-changed', refresh)
    return () => window.removeEventListener('templates-changed', refresh)
  }, [])
  return (
    <details className="expansion-panel">
      <summary>从能力模板开始</summary>
      <p>包含源码、存储约定和交互测试。选择模板不调用模型；确认方案并开始开发后才使用任务预算。</p>
      <div className="template-grid">
        {items.map((t) => (
          <article key={t.id}>
            <h3>{t.spec.product_name}</h3>
            <p>{t.spec.summary}</p>
            <small>
              模板 v{t.version} · {t.spec.acceptance_criteria.length} 条验收标准
            </small>
            <button
              disabled={busy || disabled}
              onClick={async () => {
                setBusy(true)
                setError('')
                try {
                  const d = await apiJson<{ design_id: string }>('/templates/' + t.id + '/draft', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                      model_name: model,
                      run_budget_cny: budget,
                      execution_step_limit: steps,
                    }),
                  })
                  onSelect(d.design_id)
                } catch (e) {
                  setError(e instanceof Error ? e.message : '模板创建失败')
                } finally {
                  setBusy(false)
                }
              }}
            >
              创建可编辑方案
            </button>
          </article>
        ))}
      </div>
      {error && <p className="inline-state error">{error}</p>}
    </details>
  )
}
