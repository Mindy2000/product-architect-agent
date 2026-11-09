import { useEffect, useState } from 'react'
import { apiJson } from './api'
export type RuleChoice = {
  rule_ids: string[] | null
  page: string[]
  feature: string[]
  task: string[]
}
type Rule = { id: string; title: string; content: string; scope_type?: string; targets?: string[] }
export default function RuleSelection({
  runId,
  onChange,
}: {
  runId: string
  onChange: (v: RuleChoice) => void
}) {
  const [selection, setSelection] = useState<RuleChoice>({
      rule_ids: null,
      page: [],
      feature: [],
      task: [],
    }),
    [items, setItems] = useState<Rule[]>([]),
    [warnings, setWarnings] = useState<{ message: string; key?: string }[]>([]),
    [error, setError] = useState('')
  const encoded = JSON.stringify(selection)
  useEffect(() => {
    let gone = false
    apiJson<{ items: Rule[]; warnings: { message: string; key?: string }[] }>(
      '/collaboration/projects/' + runId + '/rules/selection',
      { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: encoded },
    )
      .then((d) => {
        if (!gone) {
          setItems(d.items)
          setWarnings(d.warnings)
          setError('')
        }
      })
      .catch((e) => {
        if (!gone) setError(e.message)
      })
    return () => {
      gone = true
    }
  }, [runId, encoded])
  function update(v: RuleChoice) {
    setSelection(v)
    onChange(v)
  }
  return (
    <details className="expansion-panel">
      <summary>本次修改适用规则</summary>
      <p>
        默认仅应用项目通用规则。填写精确范围标记后选择页面、功能或任务规则；冻结后不会随长期规则改变。只提示明确键值冲突，自由文本需人工核对。
      </p>
      {(['page', 'feature', 'task'] as const).map((k) => (
        <label key={k}>
          {{ page: '页面路径', feature: '功能标记', task: '任务标记' }[k]}（逗号分隔）
          <input
            value={selection[k].join(',')}
            maxLength={1200}
            onChange={(e) =>
              update({
                ...selection,
                [k]: e.target.value.split(',').filter(Boolean),
                rule_ids: null,
              })
            }
          />
        </label>
      ))}
      {items.map((r) => (
        <label key={r.id}>
          <input
            type="checkbox"
            checked={selection.rule_ids === null || selection.rule_ids.includes(r.id)}
            onChange={(e) => {
              const current = selection.rule_ids || items.map((v) => v.id)
              update({
                ...selection,
                rule_ids: e.target.checked ? [...current, r.id] : current.filter((v) => v !== r.id),
              })
            }}
          />
          {r.title}：{r.content}
        </label>
      ))}
      {warnings.map((w, i) => (
        <p className="inline-state error" key={i}>
          {w.message} {w.key}
        </p>
      ))}
      {error && <p className="inline-state error">{error}</p>}
    </details>
  )
}
