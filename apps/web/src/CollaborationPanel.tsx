import { useEffect, useState } from 'react'
import { apiJson } from './api'
type Rule = {
  id: string
  revision: number
  title: string
  content: string
  source: string
  active: boolean
  scope_type?: string
  targets?: string[]
  constraints?: Record<string, string>
  history: { revision: number; content: string; active: boolean; updated_at: string }[]
}
type Question = {
  id: string
  status: string
  question: string
  reason: string
  options: string[]
  answer?: string
}
export default function CollaborationPanel({
  runId,
  status,
  onRefresh,
}: {
  runId: string
  status: string
  onRefresh: () => void
}) {
  const [rules, setRules] = useState<Rule[]>([]),
    [questions, setQuestions] = useState<Question[]>([]),
    [frozen, setFrozen] = useState<Rule[]>([])
  const [title, setTitle] = useState(''),
    [content, setContent] = useState(''),
    [source, setSource] = useState('用户确认'),
    [editing, setEditing] = useState<Rule | null>(null),
    [answer, setAnswer] = useState(''),
    [error, setError] = useState(''),
    [busy, setBusy] = useState(false)
  const [scopeType, setScopeType] = useState('project'),
    [targets, setTargets] = useState(''),
    [constraints, setConstraints] = useState('{}')
  const base = '/collaboration/projects/' + runId
  async function refresh() {
    const [a, b] = await Promise.all([
      apiJson<{ items: Rule[] }>(base + '/rules'),
      apiJson<{ questions: Question[]; frozen_rules: Rule[] }>('/collaboration/runs/' + runId),
    ])
    setRules(a.items)
    setQuestions(b.questions)
    setFrozen(b.frozen_rules)
  }
  useEffect(() => {
    let disposed = false
    Promise.all([
      apiJson<{ items: Rule[] }>(base + '/rules'),
      apiJson<{ questions: Question[]; frozen_rules: Rule[] }>('/collaboration/runs/' + runId),
    ])
      .then(([a, b]) => {
        if (!disposed) {
          setRules(a.items)
          setQuestions(b.questions)
          setFrozen(b.frozen_rules)
        }
      })
      .catch((e) => {
        if (!disposed) setError(String(e.message))
      })
    return () => {
      disposed = true
    }
  }, [base, runId, status])
  async function act(fn: () => Promise<void>) {
    setBusy(true)
    setError('')
    try {
      await fn()
      await refresh()
    } catch (e) {
      setError(e instanceof Error ? e.message : '操作未完成')
    } finally {
      setBusy(false)
    }
  }
  const pending = questions.find((q) => q.status === 'pending')
  return (
    <section className="expansion-panel">
      <h3>协作与项目规则</h3>
      {pending && (
        <div className="clarification-card">
          <b>需要你决定：{pending.question}</b>
          <p>{pending.reason}</p>
          <div className="preview-tools">
            {pending.options.map((o) => (
              <button disabled={busy} key={o} onClick={() => setAnswer(o)}>
                {o}
              </button>
            ))}
          </div>
          <textarea
            aria-label="澄清回答"
            value={answer}
            onChange={(e) => setAnswer(e.target.value)}
            maxLength={1200}
          />
          <button
            disabled={busy || !answer.trim() || !['paused', 'interrupted'].includes(status)}
            onClick={() =>
              void act(async () => {
                await apiJson(
                  '/collaboration/runs/' + runId + '/questions/' + pending.id + '/answer',
                  {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ answer }),
                  },
                )
                setAnswer('')
                onRefresh()
              })
            }
          >
            保存回答
          </button>
          <p>回答只解释当前范围。保存后点击任务的“继续”，才会恢复执行。</p>
        </div>
      )}
      {!!questions.length && (
        <details>
          <summary>澄清记录 · {questions.length}</summary>
          {questions.map((q) => (
            <p key={q.id}>
              {q.question} → {q.answer || '等待回答'}
            </p>
          ))}
        </details>
      )}
      <details>
        <summary>本次任务冻结的规则 · {frozen.length}</summary>
        {frozen.map((r) => (
          <p key={r.id}>
            {r.title} · 修订 {r.revision}：{r.content}
          </p>
        ))}
      </details>
      <details>
        <summary>管理长期项目规则 · {rules.length}</summary>
        <p>新方案使用当前生效规则；修改不会影响已有方案或历史任务。规则不能扩大执行权限。</p>
        {rules.map((r) => (
          <article className="rule-card" key={r.id}>
            <b>
              {r.title} · {r.active ? '生效' : '已撤销'} · 修订 {r.revision}
            </b>
            <p>{r.content}</p>
            <small>来源：{r.source}</small>
            <div>
              <button
                disabled={busy}
                onClick={() => {
                  setEditing(r)
                  setTitle(r.title)
                  setContent(r.content)
                  setSource(r.source)
                  setScopeType(r.scope_type || 'project')
                  setTargets((r.targets || []).join(','))
                  setConstraints(JSON.stringify(r.constraints || {}))
                }}
              >
                编辑
              </button>
              <button
                disabled={busy}
                onClick={() =>
                  void act(async () => {
                    await apiJson(base + '/rules/' + r.id, {
                      method: 'PUT',
                      headers: { 'Content-Type': 'application/json' },
                      body: JSON.stringify({
                        scope_type: r.scope_type || 'project',
                        targets: r.targets || [],
                        constraints: r.constraints || {},
                        title: r.title,
                        content: r.content,
                        source: r.source,
                        revision: r.revision,
                        active: !r.active,
                      }),
                    })
                  })
                }
              >
                {r.active ? '撤销规则' : '重新启用'}
              </button>
            </div>
            <details>
              <summary>历史修订</summary>
              {r.history.map((h) => (
                <p key={h.revision}>
                  #{h.revision} · {h.active ? '生效' : '撤销'}：{h.content}
                </p>
              ))}
            </details>
          </article>
        ))}
        <label>
          规则标题
          <input value={title} onChange={(e) => setTitle(e.target.value)} maxLength={100} />
        </label>
        <label>
          规则内容
          <textarea
            aria-label="规则内容"
            value={content}
            onChange={(e) => setContent(e.target.value)}
            maxLength={1200}
          />
        </label>
        <label>
          适用范围
          <select value={scopeType} onChange={(e) => setScopeType(e.target.value)}>
            <option value="project">整个项目</option>
            <option value="page">指定页面</option>
            <option value="feature">指定功能</option>
            <option value="task">指定任务</option>
          </select>
        </label>
        {scopeType !== 'project' && (
          <label>
            精确范围标记（逗号分隔）
            <input value={targets} onChange={(e) => setTargets(e.target.value)} maxLength={1200} />
          </label>
        )}
        <label>
          可检查的结构化字段（可留空对象，例如 {`{"primary_color":"blue"}`}）
          <textarea
            value={constraints}
            onChange={(e) => setConstraints(e.target.value)}
            maxLength={3000}
          />
        </label>
        <label>
          规则来源
          <input value={source} onChange={(e) => setSource(e.target.value)} maxLength={200} />
        </label>
        <button
          disabled={busy || !title.trim() || !content.trim() || !source.trim()}
          onClick={() =>
            void act(async () => {
              await apiJson(base + '/rules' + (editing ? '/' + editing.id : ''), {
                method: editing ? 'PUT' : 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                  scope_type: scopeType,
                  targets:
                    scopeType === 'project'
                      ? []
                      : targets
                          .split(',')
                          .map((v) => v.trim())
                          .filter(Boolean),
                  constraints: JSON.parse(constraints),
                  title,
                  content,
                  source,
                  active: editing?.active ?? true,
                  revision: editing?.revision ?? 0,
                }),
              })
              setEditing(null)
              setTitle('')
              setContent('')
            })
          }
        >
          {editing ? '确认保存修订' : '确认添加规则'}
        </button>
        {editing && (
          <button
            onClick={() => {
              setEditing(null)
              setTitle('')
              setContent('')
            }}
          >
            取消编辑
          </button>
        )}
      </details>
      {error && <p className="inline-state error">{error}</p>}
    </section>
  )
}
