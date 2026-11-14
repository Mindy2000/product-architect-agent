import RoleModelPicker, { type RoleModels, textModelOptions } from './RoleModels'
import RunHistory from './RunHistory'
import { statusText, stageIndex, stageText } from './runPresentation'
import { API_BASE, apiFetch } from './api'
import { useRunSelection } from './useRunSelection'
import { terminalStates } from './runTypes'
import { useEffect, useState } from 'react'
import WorkbenchCenter from './WorkbenchCenter'
import ProductPreview from './ProductPreview'
import CustomTemplate from './CustomTemplate'
import ExecutionReplay from './ExecutionReplay'
import EvidenceManager from './EvidenceManager'
import RuleSelection, { type RuleChoice } from './RuleSelection'
import CollaborationPanel from './CollaborationPanel'
import TemplatePicker from './TemplatePicker'
import DesignStudio from './DesignStudio'
import ReferenceLibrary from './ReferenceLibrary'
import KnowledgeSettings from './KnowledgeSettings'
import DifyKnowledge from './DifyKnowledge'
import KnowledgeLibrary from './KnowledgeLibrary'
import KnowledgeEvidence from './KnowledgeEvidence'
import Versions from './Versions'
import SourceViewer from './SourceViewer'
import DeliveryCenter from './DeliveryCenter'
import TaskPanel from './TaskPanel'
import TeamPanel, { agentNames } from './TeamPanel'
import ProjectLibrary from './ProjectLibrary'
import EngineeringPanel from './EngineeringPanel'
import BackendPanel from './BackendPanel'
import PublicResearch from './PublicResearch'
import WebEvidence from './WebEvidence'
import RunCosts from './RunCosts'

const stages = ['需求分析', '开发规划', '任务开发', '构建验证', '浏览器测试', '审查与修复', '交付']
const API = API_BASE
type Budget = {
  spent_cny_estimate: number
  budget_cny: number
  remaining_cny_estimate: number
  calls: number
}
type Capabilities = {
  knowledge_library?: { enabled: boolean }
  reference_materials?: { enabled: boolean }
  rag?: { mode: string }
  design_review_required?: boolean
  runtime_version?: number
  model_provider: string
  model: string
  available_models: string[]
  model_calls_enabled: boolean
  sandbox: { ready: boolean }
}

export default function App() {
  const [view, setView] = useState<'workspace' | 'projects' | 'trace' | 'settings'>('workspace')
  const [request, setRequest] = useState('')
  const [designId, setDesignId] = useState<string | null>(() => {
    const value = new URLSearchParams(window.location.search).get('design')
    return value && /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(value)
      ? value
      : null
  })
  const [referenceIds, setReferenceIds] = useState<string[]>([])
  const [knowledgeIds, setKnowledgeIds] = useState<string[]>([])
  const [remoteIds, setRemoteIds] = useState<string[]>([])
  const [researchEnabled, setResearchEnabled] = useState(false),
    [webSourceIds, setWebSourceIds] = useState<string[]>([])
  const [applicationType, setApplicationType] = useState<'frontend' | 'fullstack_crud'>('frontend')
  const [annotationSelection, setAnnotationSelection] = useState<{
    parent: string
    ids: string[]
  } | null>(null)
  const [ruleChoice, setRuleChoice] = useState<{ parent: string; value: RuleChoice } | null>(null)
  const [parentRunId, setParentRunId] = useState<string | null>(null)
  const [acceptanceProfile, setAcceptanceProfile] = useState('generated')
  const [runBudget, setRunBudget] = useState(5),
    [stepLimit, setStepLimit] = useState(40)
  const [roleModels, setRoleModels] = useState<RoleModels>({})
  const [selectedModel, setSelectedModel] = useState(() => {
    const saved = localStorage.getItem('product-architect-model')
    return textModelOptions.some((m) => m.id === saved) ? saved! : 'deepseek-flash'
  })
  useEffect(() => {
    const changed = () => {
      const next = localStorage.getItem('product-architect-model')
      if (next && textModelOptions.some((m) => m.id === next)) setSelectedModel(next)
    }
    window.addEventListener('model-default-changed', changed)
    return () => window.removeEventListener('model-default-changed', changed)
  }, [])
  const [budget, setBudget] = useState<Budget | null>(null)
  const [capabilities, setCapabilities] = useState<Capabilities | null>(null)
  const [connectionError, setConnectionError] = useState('')
  const {
    run,
    runs,
    setRun,
    refreshRun,
    updateRun,
    selectionRevision,
    isCurrentSelection,
    receiveRun,
    openFromAction,
  } = useRunSelection(refreshBudget)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [pauseNotice, setPauseNotice] = useState('')
  async function refreshBudget() {
    try {
      const r = await apiFetch(API + '/budget')
      if (r.ok) setBudget(await r.json())
    } catch {
      /* API may be starting */
    }
  }
  async function refreshCapabilities() {
    try {
      const r = await apiFetch(API + '/capabilities')
      if (r.status === 503 && r.headers.get('Retry-After')) return
      if (!r.ok) throw new Error('unavailable')
      const next = (await r.json()) as Capabilities
      setCapabilities(next)
      if (
        !localStorage.getItem('product-architect-model') &&
        next.available_models.includes(next.model)
      )
        setSelectedModel(next.model)
      setConnectionError('')
      if (!next.model_calls_enabled && !localStorage.getItem('pa-onboarding-dismissed')) {
        localStorage.setItem('pa-onboarding-dismissed', 'true')
        setView('settings')
      }
    } catch {
      setConnectionError(
        '本地服务未连接。请运行项目目录中的「启动.command」（Windows 为 start.cmd），保持启动窗口打开，然后刷新页面。',
      )
    }
  }
  useEffect(() => {
    void refreshBudget()
    void refreshCapabilities()
    const timer = window.setInterval(() => {
      void refreshCapabilities()
    }, 10000)
    return () => window.clearInterval(timer)
  }, [])
  async function cancelRun() {
    if (!run || !window.confirm('取消当前任务？当前阶段完成后停止，源码与费用记录保留。')) return
    try {
      const response = await apiFetch(API + '/runs/' + run.run_id + '/cancel', { method: 'POST' })
      if (!response.ok) throw new Error('取消失败')
      if (isCurrentSelection(selectionRevision))
        setPauseNotice('取消请求已收到，当前阶段结束后停止。')
      await updateRun(run.run_id)
    } catch (e) {
      if (isCurrentSelection(selectionRevision))
        setError(e instanceof Error ? e.message : '取消失败')
    }
  }
  async function pauseRun() {
    if (!run) return
    try {
      const response = await apiFetch(API + '/runs/' + run.run_id + '/pause', { method: 'POST' })
      if (!response.ok) throw new Error('任务当前无法暂停，请刷新状态。')
      if (isCurrentSelection(selectionRevision))
        setPauseNotice('暂停请求已收到，当前阶段完成后停止。')
    } catch (error) {
      if (isCurrentSelection(selectionRevision))
        setError(error instanceof Error ? error.message : '暂停失败')
    }
  }
  async function startRun() {
    setBusy(true)
    setError('')
    setPauseNotice('')
    try {
      if (capabilities?.design_review_required) {
        const response = await apiFetch(API + '/designs', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            request,
            application_type: applicationType,
            research_enabled: researchEnabled,
            web_source_ids: webSourceIds,
            model_name: selectedModel,
            role_models: roleModels,
            acceptance_profile: acceptanceProfile,
            parent_run_id: parentRunId,
            rule_selection: ruleChoice?.parent === parentRunId ? ruleChoice.value : undefined,
            annotation_ids:
              annotationSelection?.parent === parentRunId ? annotationSelection.ids : [],
            reference_ids: referenceIds,
            knowledge_collection_ids: knowledgeIds,
            remote_knowledge_ids: remoteIds,
            run_budget_cny: runBudget,
            execution_step_limit: stepLimit,
          }),
        })
        const data = await response.json()
        if (!response.ok)
          throw new Error(
            typeof data.detail === 'string'
              ? data.detail
              : '无法生成产品方案，请检查需求和费用设置。',
          )
        if (!isCurrentSelection(selectionRevision)) return
        setDesignId(data.design_id)
        setResearchEnabled(false)
        setWebSourceIds([])
        return
      }
      const response = await apiFetch(API + '/runs', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          request,
          application_type: applicationType,
          research_enabled: researchEnabled,
          web_source_ids: webSourceIds,
          model_name: selectedModel,
          role_models: roleModels,
          acceptance_profile: acceptanceProfile,
          parent_run_id: parentRunId,
          ...((capabilities?.runtime_version ?? 0) >= 2
            ? { run_budget_cny: runBudget, execution_step_limit: stepLimit }
            : {}),
        }),
      })
      if (!response.ok) {
        const detail = await response.json().catch(() => null)
        throw new Error(detail?.detail?.message || '本地 Agent 返回错误（' + response.status + '）')
      }
      const created = (await response.json()) as import('./runTypes').RunRecord
      if (!receiveRun(created, selectionRevision)) return
      setResearchEnabled(false)
      setWebSourceIds([])
      setRequest('')
      void refreshBudget()
    } catch (e) {
      if (isCurrentSelection(selectionRevision))
        setError(e instanceof Error ? e.message : '无法连接本地 Agent，请确认服务已启动。')
    } finally {
      setBusy(false)
      void refreshBudget()
    }
  }
  async function resumeRun() {
    if (!run) return
    setBusy(true)
    setError('')
    setPauseNotice('')
    try {
      const response = await apiFetch(API + '/runs/' + run.run_id + '/resume', { method: 'POST' })
      if (!response.ok) {
        const body = await response.json().catch(() => null)
        throw new Error(body?.detail || '恢复失败（' + response.status + '）')
      }
      await updateRun(run.run_id)
    } catch (e) {
      if (isCurrentSelection(selectionRevision))
        setError(e instanceof Error ? e.message : '恢复失败。')
    } finally {
      setBusy(false)
      void refreshBudget()
    }
  }
  const activeIndex = run
    ? run.stage === 'executor' && run.execution_mode === 'repair'
      ? 5
      : (stageIndex[run.stage] ?? 0)
    : -1
  const isTerminal = !!run && terminalStates.has(run.status)
  const repairAttempts = run
    ? Math.max(
        run.repair_round ?? 0,
        run.trace?.filter((item) => item.stage === 'repair' && item.status === 'running').length ??
          0,
      )
    : 0
  return (
    <div className="shell">
      <aside className="sidebar">
        <div className="brand">
          <div className="brand-icon">PA</div>
          <div>
            <b>Product Architect</b>
            <small>AGENT STUDIO</small>
          </div>
        </div>
        <button
          className="new-run"
          onClick={() => {
            setView('workspace')
            setRun(null)
            setReferenceIds([])
            setKnowledgeIds([])
            setRemoteIds([])
            setResearchEnabled(false)
            setWebSourceIds([])
            setDesignId(null)
            setParentRunId(null)
            setRequest('')
            setError('')
          }}
        >
          ＋　新建开发任务
        </button>
        <div className="nav-label">工作空间</div>
        <button
          className={'nav-item ' + (view === 'workspace' ? 'active' : '')}
          onClick={() => setView('workspace')}
        >
          ◈　开发工作台
        </button>
        <button
          className={'nav-item ' + (view === 'projects' ? 'active' : '')}
          onClick={() => setView('projects')}
        >
          ▤　项目记录
        </button>
        <button
          className={'nav-item ' + (view === 'trace' ? 'active' : '')}
          onClick={() => setView('trace')}
        >
          ⌁　运行 Trace
        </button>
        <button
          className={'nav-item ' + (view === 'settings' ? 'active' : '')}
          onClick={() => setView('settings')}
        >
          ⚙　设置与维护
        </button>
        <div className="sidebar-bottom">
          <div className="avatar">PA</div>
          <div>
            <b>本地工作区</b>
            <small>DeepSeek · 本地隔离执行</small>
          </div>
          <span className="status-dot" />
        </div>
      </aside>
      <main>
        <header>
          <div className="breadcrumbs">
            工作空间 <span>/</span>{' '}
            {view === 'workspace'
              ? '开发工作台'
              : view === 'projects'
                ? '项目记录'
                : view === 'settings'
                  ? '设置与维护'
                  : '运行 Trace'}
          </div>
          <div className="header-right">
            <span className="local-pill">● 本地模式</span>
            <button
              className="icon-button"
              aria-label="工作台设置"
              onClick={() => setView('settings')}
            >
              ⚙
            </button>
          </div>
        </header>
        <div className="content">
          {view === 'settings' ? (
            <WorkbenchCenter onBack={() => setView('workspace')} />
          ) : view === 'workspace' ? (
            <>
              <section className="intro">
                <div className="eyebrow">
                  PRODUCT ARCHITECT AGENT <span>·</span> 项目工作台
                </div>
                <h1>
                  把产品想法，变成
                  <br />
                  <em>可验证的真实产品。</em>
                </h1>
                <p>描述你想构建的产品，先编辑并确认产品方案，再由 Agent 逐步规划、开发并验证。</p>
              </section>
              <section className="composer">
                {connectionError && (
                  <p className="inline-state error" role="alert">
                    {connectionError}
                  </p>
                )}
                {capabilities && !capabilities.model_calls_enabled && (
                  <p className="inline-state" role="status">
                    首次使用：请先配置文字模型密钥、预算和调用许可，保存后重新启动工作台。费用由你自己的模型账户支付；视觉模型和知识库可以暂不配置。
                    <button className="text-button" onClick={() => setView('settings')}>
                      打开可视化首次使用向导
                    </button>
                  </p>
                )}
                {parentRunId && (
                  <div className="inline-state">
                    正在修改项目 {parentRunId.slice(0, 8)}，提交后创建新版本，保留原版本。
                    <button className="text-button" onClick={() => setParentRunId(null)}>
                      取消追加修改
                    </button>
                  </div>
                )}
                <div className="composer-title">
                  <div>
                    <b>描述你的产品需求</b>
                    <small>支持 1–12 个页面 · React + TypeScript · 可选独立业务后端</small>
                  </div>
                  <span className="secure">⌑　禁网沙箱</span>
                </div>
                <div className="model-picker">
                  <label htmlFor="application-type">生成应用类型</label>
                  <select
                    id="application-type"
                    value={applicationType}
                    disabled={busy || !!parentRunId}
                    onChange={(e) =>
                      setApplicationType(e.target.value as 'frontend' | 'fullstack_crud')
                    }
                  >
                    <option value="frontend">前端应用 · localStorage</option>
                    <option value="fullstack_crud">完整 CRUD 应用 · FastAPI + SQLite</option>
                  </select>
                  <label htmlFor="model-choice">本次任务的文字模型</label>
                  <select
                    id="model-choice"
                    value={selectedModel}
                    onChange={(event) => {
                      setSelectedModel(event.target.value)
                      localStorage.setItem('product-architect-model', event.target.value)
                    }}
                    disabled={busy}
                  >
                    <option value="deepseek-flash">DeepSeek Flash · 速度快，适合日常生成</option>
                    <option value="deepseek-v4-pro">DeepSeek V4 Pro · 能力更强，费用更高</option>
                    <option value="qwen3.8-flash">千问 3.8 Flash · 北京文字通道</option>
                  </select>
                  <small>仅影响新方案；角色可单独设置，共用累计与单任务预算。</small>
                  <RoleModelPicker value={roleModels} onChange={setRoleModels} disabled={busy} />
                  <label htmlFor="acceptance-choice">验收方式</label>
                  <select
                    id="acceptance-choice"
                    value={acceptanceProfile}
                    onChange={(event) => setAcceptanceProfile(event.target.value)}
                    disabled={busy}
                  >
                    <option value="generated">任务生成的交互测试</option>
                    <option value="todo-v1">To-do 独立标准验收</option>
                    <option value="todo-v2">To-do 标准验收（含状态筛选）</option>
                    <option value="notes-v1">笔记独立标准验收</option>
                    <option value="expenses-v1">记账独立标准验收</option>
                    <option value="habits-v1">习惯独立标准验收</option>
                    <option value="reading-v1">阅读清单独立标准验收</option>
                  </select>
                  {(capabilities?.runtime_version ?? 0) >= 2 && (
                    <>
                      <label htmlFor="run-budget">本次任务费用上限</label>
                      <input
                        id="run-budget"
                        type="number"
                        min="0.25"
                        max="20"
                        step="0.25"
                        value={runBudget}
                        disabled={busy}
                        onChange={(e) => setRunBudget(Number(e.target.value))}
                      />
                      <small>单位：元。与累计 ¥99 上限同时生效。</small>
                      <label htmlFor="step-limit">执行步数上限</label>
                      <input
                        id="step-limit"
                        type="number"
                        min="8"
                        max="64"
                        value={stepLimit}
                        disabled={busy}
                        onChange={(e) => setStepLimit(Number(e.target.value))}
                      />
                    </>
                  )}
                </div>
                <textarea
                  value={request}
                  onChange={(e) => setRequest(e.target.value)}
                  placeholder="例如：做一个简洁的个人任务管理工具，可以添加任务、标记完成、按优先级筛选，并在刷新后保留数据……"
                />
                {capabilities?.reference_materials?.enabled && (
                  <ReferenceLibrary
                    selected={referenceIds}
                    onChange={setReferenceIds}
                    disabled={busy}
                  />
                )}
                {capabilities?.knowledge_library?.enabled && (
                  <KnowledgeLibrary
                    selected={knowledgeIds}
                    onChange={setKnowledgeIds}
                    disabled={busy}
                  />
                )}
                <DifyKnowledge selected={remoteIds} onChange={setRemoteIds} disabled={busy} />
                <PublicResearch
                  enabled={researchEnabled}
                  onEnabled={setResearchEnabled}
                  selected={webSourceIds}
                  onChange={setWebSourceIds}
                  disabled={busy}
                />
                <div className="composer-footer">
                  <span>
                    {!capabilities
                      ? '正在连接本地服务'
                      : !capabilities.model_calls_enabled
                        ? '请启用模型调用'
                        : !capabilities.sandbox.ready
                          ? '正在准备隔离执行环境'
                          : '建议包含目标用户、核心功能和视觉偏好'}
                  </span>
                  <button
                    className="primary"
                    disabled={
                      request.trim().length < 10 ||
                      busy ||
                      ((capabilities?.runtime_version ?? 0) >= 2 &&
                        (!Number.isFinite(runBudget) ||
                          runBudget < 0.25 ||
                          runBudget > 20 ||
                          !Number.isInteger(stepLimit) ||
                          stepLimit < 8 ||
                          stepLimit > 64)) ||
                      !capabilities?.model_calls_enabled ||
                      (!capabilities.design_review_required && !capabilities.sandbox.ready)
                    }
                    onClick={startRun}
                  >
                    {busy
                      ? '任务运行中…'
                      : !capabilities
                        ? '连接中…'
                        : !capabilities.model_calls_enabled
                          ? '模型未启用'
                          : capabilities.design_review_required
                            ? '生成产品方案'
                            : !capabilities.sandbox.ready
                              ? '准备执行环境…'
                              : '开始开发'}{' '}
                    <span>→</span>
                  </button>
                </div>
                {error && <div className="inline-state error">{error}</div>}
              </section>
              {parentRunId && (
                <RuleSelection
                  key={parentRunId}
                  runId={parentRunId}
                  onChange={(value) => setRuleChoice({ parent: parentRunId, value })}
                />
              )}
              <TemplatePicker
                key={selectionRevision}
                disabled={busy || !!parentRunId}
                model={selectedModel}
                roleModels={roleModels}
                budget={runBudget}
                steps={stepLimit}
                onSelect={(id) => {
                  if (isCurrentSelection(selectionRevision)) {
                    setRun(null)
                    setDesignId(id)
                  }
                }}
              />
              {capabilities?.design_review_required && (
                <DesignStudio
                  key={selectionRevision + ':' + designId}
                  designId={designId}
                  onSelect={(id) => {
                    setRun(null)
                    setDesignId(id)
                  }}
                  onStarted={(id) => {
                    void openFromAction(id, selectionRevision).catch(() => {
                      setError('开发任务已创建，暂时无法读取进度。')
                    })
                    void refreshBudget()
                  }}
                  onCostChanged={() => void refreshBudget()}
                />
              )}
              {['interface_only', 'external_interface_only', 'dify_cloud_optional'].includes(
                capabilities?.rag?.mode ?? '',
              ) && <KnowledgeSettings />}
              <section className="lower">
                <div className="workflow card">
                  <div className="section-heading">
                    <div>
                      <b>开发流程</b>
                      <small>需求、代码、构建、浏览器验收与交付</small>
                    </div>
                    <span className="tiny-tag">最多修复 3 轮</span>
                  </div>
                  <div className="steps">
                    {stages.map((label, i) => {
                      const complete = !!run && (run.status === 'completed' || i < activeIndex)
                      const current = !!run && !isTerminal && i === activeIndex
                      return (
                        <div
                          className={
                            'step ' + (complete ? 'completed ' : '') + (current ? 'current' : '')
                          }
                          key={label}
                        >
                          <div className="step-node">
                            {complete ? '✓' : current ? '●' : String(i + 1).padStart(2, '0')}
                          </div>
                          <span>{label}</span>
                          {i < stages.length - 1 && <div className="step-line" />}
                        </div>
                      )
                    })}
                  </div>
                  {run && (
                    <>
                      <div
                        className={
                          'inline-state ' +
                          (run.status.startsWith('failed') || run.status.startsWith('blocked')
                            ? 'error'
                            : '')
                        }
                      >
                        <span className="pulse" />
                        {statusText[run.status] ?? run.stage} · {run.run_id.slice(0, 8)}
                        {repairAttempts > 0 ? ' · 自动修复尝试 ' + repairAttempts + '/3 轮' : ''}
                      </div>
                      <div className="run-detail">
                        <EngineeringPanel runId={run.run_id} />
                        <WebEvidence kind="runs" id={run.run_id} />
                        {run.product_spec?.application_type === 'fullstack_crud' && isTerminal && (
                          <BackendPanel
                            key={run.run_id + '-backend'}
                            runId={run.run_id}
                            onRestored={(id) => {
                              void openFromAction(id, selectionRevision).catch(() => {
                                setError('数据副本已建立，请从项目记录打开。')
                              })
                            }}
                          />
                        )}
                        {run.source_import && (
                          <p className="inline-state">
                            这是导入的暂停项目，源码尚未运行或验收。点击“在此项目上继续修改”，确认产品方案后再开始开发。
                          </p>
                        )}
                        {run.operator_assisted && (
                          <div className="inline-state">
                            本次由 Codex 辅助完成 · {run.operator_note}
                          </div>
                        )}
                        {!isTerminal && (
                          <button
                            className="text-button"
                            disabled={!!pauseNotice}
                            onClick={pauseRun}
                          >
                            暂停任务
                          </button>
                        )}
                        {!isTerminal && (
                          <button className="text-button" onClick={() => void cancelRun()}>
                            取消任务
                          </button>
                        )}
                        {pauseNotice && !isTerminal && <div>{pauseNotice}</div>}
                        {(run.status === 'completed' ||
                          (run.status === 'paused' &&
                            (run.source_import || run.local_data_restored))) && (
                          <button
                            className="text-button"
                            disabled={busy}
                            onClick={() => {
                              setParentRunId(run.run_id)
                              setApplicationType(run.product_spec?.application_type ?? 'frontend')
                              setReferenceIds([])
                              setKnowledgeIds([])
                              setRemoteIds([])
                              setResearchEnabled(false)
                              setWebSourceIds([])
                              setDesignId(null)
                              setRequest('')
                              setView('workspace')
                              window.scrollTo({ top: 0, behavior: 'smooth' })
                            }}
                          >
                            在此项目上继续修改 →
                          </button>
                        )}
                        {run.parent_run_id && (
                          <button
                            className="text-button"
                            onClick={() => {
                              void refreshRun(run.parent_run_id!)
                            }}
                          >
                            查看上一版本 {run.parent_run_id.slice(0, 8)}
                          </button>
                        )}
                        {run.product_spec && (
                          <>
                            <b>{run.product_spec.product_name} · 需求摘要</b>
                            <div>{run.product_spec.summary}</div>
                            <div>目标用户：{run.product_spec.target_users?.join('、')}</div>
                            <div>页面：{run.product_spec.pages?.join('、')}</div>
                            <div>核心功能：{run.product_spec.core_features?.join('、')}</div>
                          </>
                        )}
                        {run.acceptance_criteria?.length > 0 && (
                          <>
                            <b>验收标准</b>
                            {run.acceptance_criteria.map((c) => (
                              <div key={c}>•　{c}</div>
                            ))}
                          </>
                        )}
                        {run.plan?.length > 0 && (
                          <>
                            <b>开发计划</b>
                            {run.plan.map((p, i) => (
                              <div key={p}>
                                {i + 1}.　{p}
                              </div>
                            ))}
                          </>
                        )}
                        <RunCosts
                          key={run.run_id + '-costs'}
                          runId={run.run_id}
                          status={run.status}
                        />
                        <TeamPanel run={run} />
                        {run.executor_tasks && (
                          <TaskPanel
                            key={run.run_id + '-tasks'}
                            runId={run.run_id}
                            tasks={run.executor_tasks}
                            turns={run.team_steps ?? run.executor_turns ?? 0}
                            limit={run.execution_step_limit ?? 40}
                            feedback={run.executor_feedback}
                          />
                        )}
                        {!!run.knowledge_sources?.length && (
                          <KnowledgeEvidence
                            key={run.run_id + '-knowledge'}
                            kind="runs"
                            id={run.run_id}
                          />
                        )}
                        {(run.build_status || run.test_status) && (
                          <>
                            <b>验证状态</b>
                            <div>
                              构建：
                              {run.build_status === 'passed'
                                ? '通过'
                                : run.build_status === 'unverified'
                                  ? '待重新核对'
                                  : run.build_status === 'failed'
                                    ? '失败'
                                    : '等待中'}
                              　·　Playwright：
                              {run.test_status === 'passed'
                                ? '通过'
                                : run.test_status === 'unverified'
                                  ? '待重新核对'
                                  : run.test_status === 'failed'
                                    ? '失败'
                                    : run.test_status === 'skipped'
                                      ? '已跳过'
                                      : '等待中'}
                            </div>
                          </>
                        )}
                        {run.last_error && <div className="error-detail">{run.last_error}</div>}
                        {run.build_output && (
                          <details className="run-log">
                            <summary>查看构建日志</summary>
                            <pre>{run.build_output}</pre>
                          </details>
                        )}
                        {run.test_output && (
                          <details className="run-log">
                            <summary>查看 Playwright 测试日志</summary>
                            <pre>{run.test_output}</pre>
                          </details>
                        )}
                        {run.trace?.length > 0 && (
                          <>
                            <b>运行 Trace</b>
                            {run.trace.map((item, i) => (
                              <div className="trace-row" key={item.stage + '-' + i}>
                                ·　
                                {item.agent_id
                                  ? `${agentNames[item.agent_id] ?? item.agent_id} · `
                                  : ''}
                                {stageText[item.stage] ?? item.stage}：{item.summary}
                                {item.estimated_cost_cny !== undefined
                                  ? '（约 ¥' + item.estimated_cost_cny.toFixed(4) + '）'
                                  : ''}
                              </div>
                            ))}
                          </>
                        )}
                        {(isTerminal || !!run.executor_tasks) && (
                          <SourceViewer
                            key={run.run_id + '-sources'}
                            runId={run.run_id}
                            hasDiff={!!run.artifact_paths?.git_diff}
                          />
                        )}
                        {isTerminal && (
                          <Versions
                            key={selectionRevision}
                            runId={run.run_id}
                            onRestored={(next) => {
                              if (receiveRun(next, selectionRevision)) setParentRunId(null)
                            }}
                          />
                        )}
                        {isTerminal && (
                          <DeliveryCenter key={run.run_id + '-delivery'} runId={run.run_id} />
                        )}
                        <CustomTemplate
                          key={selectionRevision + '-custom-template'}
                          runId={run.run_id}
                        />
                        <ExecutionReplay key={selectionRevision + '-replay'} runId={run.run_id} />
                        <EvidenceManager key={selectionRevision + '-records'} runId={run.run_id} />
                        <CollaborationPanel
                          key={selectionRevision + '-collaboration'}
                          runId={run.run_id}
                          status={run.status}
                          onRefresh={() => void updateRun(run.run_id).catch(() => {})}
                        />
                        {run.build_status === 'passed' && (
                          <ProductPreview
                            key={selectionRevision + '-preview'}
                            onDraft={
                              run.status === 'completed'
                                ? (ids, text) => {
                                    if (!isCurrentSelection(selectionRevision)) return
                                    setParentRunId(run.run_id)
                                    setApplicationType(
                                      run.product_spec?.application_type ?? 'frontend',
                                    )
                                    setAnnotationSelection({ parent: run.run_id, ids })
                                    setRequest('根据选中的页面意见修改，保留其他功能：\n' + text)
                                    setDesignId(null)
                                    window.scrollTo({ top: 0, behavior: 'smooth' })
                                  }
                                : undefined
                            }
                            runId={run.run_id}
                            staticPreview={!!run.artifact_paths?.preview}
                          />
                        )}
                        {run.artifact_paths && (
                          <>
                            <b>交付文件</b>
                            <div className="artifact-links">
                              <a href={API + '/runs/' + run.run_id + '/download'}>下载源码 ZIP</a>
                              <a href={API + '/runs/' + run.run_id + '/artifact/report'}>
                                交付报告
                              </a>
                              <a href={API + '/runs/' + run.run_id + '/artifact/git_diff'}>
                                Git diff
                              </a>
                              <a href={API + '/runs/' + run.run_id + '/artifact/trace'}>
                                Trace JSON
                              </a>
                              {run.artifact_paths.product_spec && (
                                <a href={API + '/runs/' + run.run_id + '/artifact/product_spec'}>
                                  产品方案
                                </a>
                              )}
                              {run.artifact_paths.manifest && (
                                <a href={API + '/runs/' + run.run_id + '/artifact/manifest'}>
                                  文件清单
                                </a>
                              )}
                            </div>
                          </>
                        )}
                        {run.can_resume && !busy && (
                          <button className="text-button" onClick={resumeRun}>
                            从 SQLite checkpoint 恢复　→
                          </button>
                        )}
                      </div>
                    </>
                  )}
                </div>
                <div className="settings card">
                  <div className="section-heading">
                    <div>
                      <b>模型与资源</b>
                      <small>当前模型适配与本地费用保护</small>
                    </div>
                    <span className="setting-icon">✳</span>
                  </div>
                  <label>本次选择</label>
                  <div className="model-name">
                    {capabilities?.model_provider ?? 'deepseek'}　/　{selectedModel}
                  </div>
                  <div className="budget">
                    <span>模型调用</span>
                    <b>{capabilities?.model_calls_enabled ? '已启用' : '已关闭'}</b>
                  </div>
                  <div className="budget">
                    <span>隔离执行环境</span>
                    <b>{capabilities?.sandbox.ready ? '已就绪' : '准备中'}</b>
                  </div>
                  <div className="budget">
                    <span>累计测试预算</span>
                    <b>
                      ¥{budget?.spent_cny_estimate.toFixed(4) ?? '0.0000'}{' '}
                      <i>/ ¥{budget?.budget_cny ?? 99}</i>
                    </b>
                  </div>
                  <div className="budget">
                    <span>已调用次数</span>
                    <b>
                      {budget?.calls ?? 0} <i>次</i>
                    </b>
                  </div>
                  <div className="budget-note">
                    按高峰价估算并预留汇率缓冲；达到上限后自动阻止新调用。
                  </div>
                </div>
              </section>
              <div className="footnote">
                <span>⌑</span>{' '}
                生成代码仅挂载任务工作区　·　隔离容器禁网　·　依赖安装在固定镜像中完成
              </div>
            </>
          ) : (
            <section className="card history-page">
              <div className="section-heading">
                <div>
                  <b>{view === 'projects' ? '项目记录' : '运行 Trace'}</b>
                  <small>
                    {view === 'projects'
                      ? '搜索、筛选与归档本机开发任务'
                      : '选择一项运行查看每个阶段的状态与模型费用'}
                  </small>
                </div>
              </div>
              {view === 'projects' ? (
                <ProjectLibrary
                  onOpen={async (id) => {
                    if (await refreshRun(id)) setView('workspace')
                  }}
                />
              ) : (
                <RunHistory runs={runs} />
              )}
            </section>
          )}
        </div>
      </main>
    </div>
  )
}
