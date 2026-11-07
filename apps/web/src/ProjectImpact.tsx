import { API_BASE, apiFetch } from './api'
import { useState } from 'react'
import MaintenanceJob, { startLocalJob } from './MaintenanceJob'
const API = API_BASE
type Impact = {
  import_affected_files?: string[]
  change_request: string
  files: { added: string[]; modified: string[]; deleted: string[] }
  changed_test_files: string[]
  page_impacts: { page: string; path: string; files: string[]; basis: string }[]
  acceptance_criteria: string[]
  acceptance_note: string
  scope_note: string
}
export default function ProjectImpact({ runId }: { runId: string }) {
  const [plan, setPlan] = useState<{
      analysis: {
        affected_files: string[]
        pages: { route: string; affected: boolean }[]
        basis: string
      }
    } | null>(null),
    [jobId, setJobId] = useState('')
  async function regressionPlan() {
    try {
      const r = await apiFetch(`${API}/projects/${runId}/regression-plan`)
      const value = await r.json()
      if (!r.ok) throw new Error(value.detail ?? '无法准备回归')
      setPlan(value)
    } catch (e) {
      setError(e instanceof Error ? e.message : '准备失败')
    }
  }
  async function regressionRun() {
    try {
      const job = await startLocalJob({ kind: 'regression', run_id: runId })
      setJobId(job.id)
    } catch (e) {
      setError(e instanceof Error ? e.message : '启动失败')
    }
  }
  const [data, setData] = useState<Impact | null>(null),
    [error, setError] = useState(''),
    [busy, setBusy] = useState(false)
  async function load() {
    if (data) return
    setBusy(true)
    setError('')
    try {
      const response = await apiFetch(`${API}/projects/${runId}/impact`)
      const body = await response.json()
      if (!response.ok) throw new Error(body.detail ?? '无法读取变更影响。')
      setData(body)
    } catch (e) {
      setError(e instanceof Error ? e.message : '读取失败。')
    } finally {
      setBusy(false)
    }
  }
  return (
    <details
      className="project-impact"
      onToggle={(e) => {
        if (e.currentTarget.open) void load()
      }}
    >
      <summary>变更影响与验收关联</summary>
      {busy && <p>正在比对父子版本源码…</p>}
      {error && (
        <p className="inline-state error" role="alert">
          {error}
        </p>
      )}
      {data && (
        <>
          <p>
            <b>本次追加需求</b>
            <br />
            {data.change_request || '未保存单独的变更说明。'}
          </p>
          <p>
            <b>变更文件</b>
          </p>
          {(['added', 'modified', 'deleted'] as const).map((kind) => (
            <div key={kind}>
              <small>
                {kind === 'added' ? '新增' : kind === 'modified' ? '修改' : '删除'}（
                {data.files[kind].length}）
              </small>
              {data.files[kind].length ? (
                <ul>
                  {data.files[kind].map((name) => (
                    <li key={name}>
                      <code>{name}</code>
                    </li>
                  ))}
                </ul>
              ) : (
                <p>无</p>
              )}
            </div>
          ))}
          <p>
            <b>可能涉及的页面（按路由引用匹配）</b>
          </p>
          {data.page_impacts.length ? (
            data.page_impacts.map((page) => (
              <div className="impact-page" key={page.path}>
                <b>{page.page}</b>
                <small>{page.basis}</small>
                <ul>
                  {page.files.map((file) => (
                    <li key={file}>
                      <code>{file}</code>
                    </li>
                  ))}
                </ul>
              </div>
            ))
          ) : (
            <p>当前方案没有页面结构记录；共享组件或间接影响需人工检查。</p>
          )}
          <p>
            <b>通过相对导入可能间接受影响的文件</b>
          </p>
          <ul>
            {data.import_affected_files?.map((file) => (
              <li key={file}>
                <code>{file}</code>
              </li>
            ))}
          </ul>
          <p>
            <b>涉及的测试文件</b>
          </p>
          {data.changed_test_files.length ? (
            <ul>
              {data.changed_test_files.map((file) => (
                <li key={file}>
                  <code>{file}</code>
                </li>
              ))}
            </ul>
          ) : (
            <p>未修改测试文件。</p>
          )}
          <p>
            <b>原验收标准（供复核）</b>
          </p>
          <ul>
            {data.acceptance_criteria.map((criterion, index) => (
              <li key={index}>{criterion}</li>
            ))}
          </ul>
          <button onClick={() => void regressionPlan()}>预览持续维护回归计划</button>
          {plan && (
            <div className="maintenance-regression">
              <p>{plan.analysis.basis}</p>
              <p>受影响文件：{plan.analysis.affected_files.join('、') || '无源码变化'}</p>
              {plan.analysis.pages.map((page) => (
                <p key={page.route}>
                  {page.route} · {page.affected ? '静态映射命中' : '未找到静态命中，仍运行全套回归'}
                </p>
              ))}
              <p>
                会在两份独立副本中完整构建和测试父子版本，比较新增/已有问题；不会改写原项目或调用模型。
              </p>
              <button onClick={() => void regressionRun()}>运行父子版本回归（不调用模型）</button>
            </div>
          )}
          {jobId && <MaintenanceJob id={jobId} />}
          <small>{data.acceptance_note}</small>
          <p className="impact-note">{data.scope_note}</p>
        </>
      )}
    </details>
  )
}
