import { useEffect, useState } from 'react'
import ProductPreview from './ProductPreview'
import DesignStudio from './DesignStudio'
import ReferenceLibrary from './ReferenceLibrary'
import KnowledgeSettings from './KnowledgeSettings'
import DifyKnowledge from './DifyKnowledge'
import KnowledgeLibrary, {type KnowledgeSource} from './KnowledgeLibrary'
import KnowledgeEvidence from './KnowledgeEvidence'
import Versions from './Versions'
import SourceViewer from './SourceViewer'
import DeliveryCenter from './DeliveryCenter'
import TaskPanel, {type ExecutionTask} from './TaskPanel'
import ProjectLibrary from './ProjectLibrary'
import EngineeringPanel from './EngineeringPanel'
import BackendPanel from './BackendPanel'
import PublicResearch from './PublicResearch'
import WebEvidence from './WebEvidence'
import RunCosts from './RunCosts'

const stages = ['需求分析', '开发规划', '任务开发', '构建验证', '浏览器测试', '自动修复', '交付']
const API = 'http://127.0.0.1:8000/api/v1'
const terminalStates = new Set(['completed', 'failed_validation', 'failed', 'failed_model', 'failed_sandbox', 'blocked_budget', 'blocked_configuration', 'interrupted', 'paused', 'cancelled'])
type TraceEvent = { stage:string; status:string; summary:string; estimated_cost_cny?:number; input_tokens?:number; output_tokens?:number; at?:string }
type RunRecord = {
  local_data_restored?:boolean;source_import?:boolean;operator_assisted?:boolean;operator_note?:string;
  knowledge_sources?:KnowledgeSource[];
  runtime_version?:number; executor_tasks?:ExecutionTask[]; executor_turns?:number; execution_step_limit?:number; executor_feedback?:string; execution_mode?:string
  run_id:string; acceptance_criteria:string[]; plan:string[]; stage:string; status:string; repair_round:number; parent_run_id?:string; model_name?:string; can_resume?:boolean; user_request?:string; created_at?:string
  product_spec?:{application_type?:'frontend'|'fullstack_crud';product_name:string;summary:string;target_users:string[];pages:string[];core_features:string[]}
  trace:TraceEvent[]; build_status?:string; test_status?:string; build_output?:string; test_output?:string; last_error?:string; artifact_paths?:Record<string,string>
}
type Budget = {spent_cny_estimate:number; budget_cny:number; remaining_cny_estimate:number; calls:number}
type Capabilities = {knowledge_library?:{enabled:boolean};reference_materials?:{enabled:boolean};rag?:{mode:string};design_review_required?:boolean;runtime_version?:number;model_provider:string;model:string;available_models:string[];model_calls_enabled:boolean;sandbox:{ready:boolean}}

const statusText:Record<string,string> = {
  cancelled:'已取消', paused:'已暂停', queued:'排队中', running:'执行中', completed:'验收通过', failed_validation:'交付完成，验收未通过',
  failed:'运行失败', failed_model:'模型调用失败', failed_sandbox:'隔离环境失败', interrupted:'服务已中断',
  blocked_budget:'预算达到上限', blocked_configuration:'模型配置待完成'
}
const stageIndex:Record<string,number> = {
  queued:0, requirements:0, planning:1, sandbox:2, sandbox_ready:2, generation:2, project_setup:2, executor:2,
  build:3, browser_test:4, repair:5, diagnosis:5, replanning:1, delivery:6
}
const stageText:Record<string,string> = {knowledge:'知识库证据',references:'参考资料',product_design:'产品方案',queued:'已排队',requirements:'需求分析',planning:'开发规划',sandbox:'准备隔离环境',sandbox_ready:'隔离环境就绪',generation:'代码生成',project_setup:'准备项目文件',executor:'任务执行',tool:'工具操作',build:'构建验证',browser_test:'浏览器测试',diagnosis:'失败诊断',replanning:'重新规划',repair:'自动修复',delivery:'交付'}

export default function App() {
  const [view, setView] = useState<'workspace'|'projects'|'trace'>('workspace')
  const [request, setRequest] = useState('')
  const [designId,setDesignId]=useState<string|null>(()=>{const value=new URLSearchParams(window.location.search).get('design');return value&&/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(value)?value:null})
  const [referenceIds,setReferenceIds]=useState<string[]>([])
  const [knowledgeIds,setKnowledgeIds]=useState<string[]>([])
  const [remoteIds,setRemoteIds]=useState<string[]>([])
  const [researchEnabled,setResearchEnabled]=useState(false),[webSourceIds,setWebSourceIds]=useState<string[]>([])
  const [applicationType,setApplicationType]=useState<'frontend'|'fullstack_crud'>('frontend')
  const [parentRunId,setParentRunId] = useState<string|null>(null)
  const [acceptanceProfile,setAcceptanceProfile] = useState('generated')
  const [runBudget,setRunBudget]=useState(5),[stepLimit,setStepLimit]=useState(40)
  const [selectedModel, setSelectedModel] = useState(() => {
    const saved = localStorage.getItem('product-architect-model')
    return saved === 'deepseek-v4-pro' ? saved : 'deepseek-flash'
  })
  const [budget, setBudget] = useState<Budget | null>(null)
  const [capabilities, setCapabilities] = useState<Capabilities | null>(null)
  const [run, setRun] = useState<RunRecord | null>(null)
  const [runs, setRuns] = useState<RunRecord[]>([])
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [pauseNotice,setPauseNotice] = useState('')
  async function refreshBudget() {
    try { const r=await fetch(API+'/budget'); if(r.ok) setBudget(await r.json()) } catch { /* API may be starting */ }
  }
  async function refreshCapabilities() {
    try { const r=await fetch(API+'/capabilities'); if(r.ok) setCapabilities(await r.json()) } catch { /* API may be starting */ }
  }
  async function refreshRun(id:string) {
    const r=await fetch(API+'/runs/'+id)
    if(!r.ok) throw new Error('无法读取任务进度。')
    const next=await r.json() as RunRecord
    setRun(next)
    setRuns(items=>[next,...items.filter(item=>item.run_id!==id)])
    return next
  }
  useEffect(() => {
    void refreshBudget()
    void refreshCapabilities()
    fetch(API+'/runs').then(r => r.ok ? r.json() : []).then((items:RunRecord[]) => { setRuns(items); if (items.length) setRun(items[0]) }).catch(() => {})
    const timer=window.setInterval(()=>{void refreshCapabilities()},10000)
    return ()=>window.clearInterval(timer)
  }, [])
  useEffect(() => {
    if(!run || terminalStates.has(run.status)) return
    const source = new EventSource(API+'/runs/'+run.run_id+'/events')
    source.onmessage = event => {
      const next = JSON.parse(event.data) as RunRecord
      setRun(next)
      setRuns(items=>[next,...items.filter(item=>item.run_id!==next.run_id)])
      if(terminalStates.has(next.status)) { source.close(); void refreshBudget() }
    }
    return ()=>source.close()
  },[run?.run_id, run ? terminalStates.has(run.status) : true])
  async function cancelRun(){
    if(!run||!window.confirm('取消当前任务？当前阶段完成后停止，源码与费用记录保留。'))return
    try{const response=await fetch(API+'/runs/'+run.run_id+'/cancel',{method:'POST'});if(!response.ok)throw new Error('取消失败');setPauseNotice('取消请求已收到，当前阶段结束后停止。');await refreshRun(run.run_id)}catch(e){setError(e instanceof Error?e.message:'取消失败')}
  }
  async function pauseRun() {
    if(!run) return
    try {
      const response = await fetch(API+'/runs/'+run.run_id+'/pause',{method:'POST'})
      if(!response.ok) throw new Error('任务当前无法暂停，请刷新状态。')
      setPauseNotice('暂停请求已收到，当前阶段完成后停止。')
    } catch(error) { setError(error instanceof Error ? error.message : '暂停失败') }
  }
  async function startRun() {
    setBusy(true); setError('');setPauseNotice('')
    try {
      if(capabilities?.design_review_required){
        const response=await fetch(API+'/designs',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({request,application_type:applicationType,research_enabled:researchEnabled,web_source_ids:webSourceIds,model_name:selectedModel,acceptance_profile:acceptanceProfile,parent_run_id:parentRunId,reference_ids:referenceIds,knowledge_collection_ids:knowledgeIds,remote_knowledge_ids:remoteIds,run_budget_cny:runBudget,execution_step_limit:stepLimit})})
        const data=await response.json()
        if(!response.ok)throw new Error(typeof data.detail==='string'?data.detail:'无法生成产品方案，请检查需求和费用设置。')
        setDesignId(data.design_id);setResearchEnabled(false);setWebSourceIds([]);return
      }
      const response = await fetch(API+'/runs', { method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({request,application_type:applicationType,research_enabled:researchEnabled,web_source_ids:webSourceIds,model_name:selectedModel,acceptance_profile:acceptanceProfile,parent_run_id:parentRunId,...(capabilities?.runtime_version===2?{run_budget_cny:runBudget,execution_step_limit:stepLimit}:{})}) })
      if(!response.ok) { const detail=await response.json().catch(()=>null); throw new Error(detail?.detail?.message || ('本地 Agent 返回错误（'+response.status+'）')) }
      const created=await response.json() as RunRecord
      setResearchEnabled(false);setWebSourceIds([]);setRun(created); setRuns(items=>[created,...items.filter(item=>item.run_id!==created.run_id)]); setRequest(''); void refreshBudget()
      let current=created
      for(let attempt=0; attempt<600 && !terminalStates.has(current.status); attempt++) {
        await new Promise(resolve=>setTimeout(resolve,1500))
        current=await refreshRun(created.run_id)
        if(attempt%4===0) void refreshBudget()
      }
      if(!terminalStates.has(current.status)) setError('任务仍在运行，可以稍后从项目记录中恢复查看。')
    } catch(e) { setError(e instanceof Error ? e.message : '无法连接本地 Agent，请确认服务已启动。') }
    finally { setBusy(false); void refreshBudget() }
  }
  async function resumeRun() {
    if(!run) return
    setBusy(true); setError('');setPauseNotice('')
    try {
      const response=await fetch(API+'/runs/'+run.run_id+'/resume',{method:'POST'})
      if(!response.ok) { const body=await response.json().catch(()=>null); throw new Error(body?.detail || ('恢复失败（'+response.status+'）')) }
      let current=await refreshRun(run.run_id)
      for(let attempt=0;attempt<600 && !terminalStates.has(current.status);attempt++) {
        await new Promise(resolve=>setTimeout(resolve,1500))
        current=await refreshRun(run.run_id)
      }
    } catch(e) { setError(e instanceof Error ? e.message : '恢复失败。') }
    finally { setBusy(false); void refreshBudget() }
  }
  const activeIndex=run ? (run.stage==='executor'&&run.execution_mode==='repair'?5:stageIndex[run.stage] ?? 0) : -1
  const isTerminal=!!run && terminalStates.has(run.status)
  const repairAttempts=run ? Math.max(run.repair_round ?? 0, run.trace?.filter(item=>item.stage==='repair'&&item.status==='running').length ?? 0) : 0
  return <div className="shell">
    <aside className="sidebar"><div className="brand"><div className="brand-icon">PA</div><div><b>Product Architect</b><small>AGENT STUDIO</small></div></div>
      <button className="new-run" onClick={() => { setView('workspace'); setRun(null); setReferenceIds([]); setKnowledgeIds([]);setRemoteIds([]);setResearchEnabled(false);setWebSourceIds([]); setDesignId(null); setParentRunId(null); setRequest(''); setError('') }}>＋　新建开发任务</button>
      <div className="nav-label">工作空间</div><button className={'nav-item '+(view==='workspace'?'active':'')} onClick={()=>setView('workspace')}>◈　开发工作台</button><button className={'nav-item '+(view==='projects'?'active':'')} onClick={()=>setView('projects')}>▤　项目记录</button><button className={'nav-item '+(view==='trace'?'active':'')} onClick={()=>setView('trace')}>⌁　运行 Trace</button>
      <div className="sidebar-bottom"><div className="avatar">PA</div><div><b>本地工作区</b><small>DeepSeek · 本地隔离执行</small></div><span className="status-dot" /></div>
    </aside>
    <main><header><div className="breadcrumbs">工作空间 <span>/</span> {view==='workspace'?'开发工作台':view==='projects'?'项目记录':'运行 Trace'}</div><div className="header-right"><span className="local-pill">● 本地模式</span><button className="icon-button" aria-label="模型设置" onClick={()=>{setView('workspace');window.setTimeout(()=>document.getElementById('model-choice')?.focus(),0)}}>⚙</button></div></header>
      <div className="content">{view==='workspace'?<><section className="intro"><div className="eyebrow">PRODUCT ARCHITECT AGENT <span>·</span> 项目工作台</div><h1>把产品想法，变成<br/><em>可验证的真实产品。</em></h1><p>描述你想构建的产品，先编辑并确认产品方案，再由 Agent 逐步规划、开发并验证。</p></section>
        <section className="composer">{parentRunId&&<div className="inline-state">正在修改项目 {parentRunId.slice(0,8)}，提交后创建新版本，保留原版本。<button className="text-button" onClick={()=>setParentRunId(null)}>取消追加修改</button></div>}<div className="composer-title"><div><b>描述你的产品需求</b><small>支持 1–12 个页面 · React + TypeScript · 可选独立业务后端</small></div><span className="secure">⌑　禁网沙箱</span></div><div className="model-picker"><label htmlFor="application-type">生成应用类型</label><select id="application-type" value={applicationType} disabled={busy||!!parentRunId} onChange={e=>setApplicationType(e.target.value as 'frontend'|'fullstack_crud')}><option value="frontend">前端应用 · localStorage</option><option value="fullstack_crud">完整 CRUD 应用 · FastAPI + SQLite（待验收）</option></select><label htmlFor="model-choice">本次任务的文字模型</label><select id="model-choice" value={selectedModel} onChange={event=>{setSelectedModel(event.target.value);localStorage.setItem('product-architect-model',event.target.value)}} disabled={busy}><option value="deepseek-flash">DeepSeek Flash · 速度快，适合日常生成</option><option value="deepseek-v4-pro">DeepSeek V4 Pro · 能力更强，费用更高</option></select><small>仅影响新任务；Pro 按较高费率预留预算。</small><label htmlFor="acceptance-choice">验收方式</label><select id="acceptance-choice" value={acceptanceProfile} onChange={event=>setAcceptanceProfile(event.target.value)} disabled={busy}><option value="generated">任务生成的交互测试</option><option value="todo-v1">To-do 独立标准验收</option><option value="todo-v2">To-do 标准验收（含状态筛选）</option><option value="notes-v1">笔记独立标准验收</option><option value="expenses-v1">记账独立标准验收</option><option value="habits-v1">习惯独立标准验收</option><option value="reading-v1">阅读清单独立标准验收</option></select>{capabilities?.runtime_version===2&&<><label htmlFor="run-budget">本次任务费用上限</label><input id="run-budget" type="number" min="0.25" max="20" step="0.25" value={runBudget} disabled={busy} onChange={e=>setRunBudget(Number(e.target.value))}/><small>单位：元。与累计 ¥99 上限同时生效。</small><label htmlFor="step-limit">执行步数上限</label><input id="step-limit" type="number" min="8" max="64" value={stepLimit} disabled={busy} onChange={e=>setStepLimit(Number(e.target.value))}/></>}</div><textarea value={request} onChange={e => setRequest(e.target.value)} placeholder="例如：做一个简洁的个人任务管理工具，可以添加任务、标记完成、按优先级筛选，并在刷新后保留数据……"/>{capabilities?.reference_materials?.enabled&&<ReferenceLibrary selected={referenceIds} onChange={setReferenceIds} disabled={busy}/>}{capabilities?.knowledge_library?.enabled&&<KnowledgeLibrary selected={knowledgeIds} onChange={setKnowledgeIds} disabled={busy}/>}<DifyKnowledge selected={remoteIds} onChange={setRemoteIds} disabled={busy}/><PublicResearch enabled={researchEnabled} onEnabled={setResearchEnabled} selected={webSourceIds} onChange={setWebSourceIds} disabled={busy}/><div className="composer-footer"><span>{!capabilities?'正在连接本地服务':!capabilities.model_calls_enabled?'请启用模型调用':!capabilities.sandbox.ready?'正在准备隔离执行环境':'建议包含目标用户、核心功能和视觉偏好'}</span><button className="primary" disabled={request.trim().length<10||busy||(capabilities?.runtime_version===2&&(!Number.isFinite(runBudget)||runBudget<0.25||runBudget>20||!Number.isInteger(stepLimit)||stepLimit<8||stepLimit>64))||!capabilities?.model_calls_enabled||(!capabilities.design_review_required&&!capabilities.sandbox.ready)} onClick={startRun}>{busy?'任务运行中…':!capabilities?'连接中…':!capabilities.model_calls_enabled?'模型未启用':capabilities.design_review_required?'生成产品方案':!capabilities.sandbox.ready?'准备执行环境…':'开始开发'} <span>→</span></button></div>{error&&<div className="inline-state error">{error}</div>}</section>
        {capabilities?.design_review_required&&<DesignStudio designId={designId} onSelect={setDesignId} onStarted={id=>{void refreshRun(id).catch(()=>setError('开发任务已创建，暂时无法读取进度。'));void refreshBudget()}} onCostChanged={()=>void refreshBudget()}/>}
        {['interface_only','external_interface_only','dify_cloud_optional'].includes(capabilities?.rag?.mode??'')&&<KnowledgeSettings/>}
        <section className="lower"><div className="workflow card"><div className="section-heading"><div><b>开发流程</b><small>需求、代码、构建、浏览器验收与交付</small></div><span className="tiny-tag">最多修复 3 轮</span></div>
          <div className="steps">{stages.map((label,i)=>{const complete=!!run&&(run.status==='completed'||i<activeIndex);const current=!!run&&!isTerminal&&i===activeIndex;return <div className={'step '+(complete?'completed ':'')+(current?'current':'')} key={label}><div className="step-node">{complete?'✓':current?'●':String(i+1).padStart(2,'0')}</div><span>{label}</span>{i<stages.length-1&&<div className="step-line"/>}</div>})}</div>
          {run&&<><div className={'inline-state '+(run.status.startsWith('failed')||run.status.startsWith('blocked')?'error':'')}><span className="pulse"/>{statusText[run.status]??run.stage} · {run.run_id.slice(0,8)}{repairAttempts>0?' · 自动修复尝试 '+repairAttempts+'/3 轮':''}</div>
            <div className="run-detail"><EngineeringPanel runId={run.run_id}/><WebEvidence kind="runs" id={run.run_id}/>{run.product_spec?.application_type==='fullstack_crud'&&isTerminal&&<BackendPanel key={run.run_id+'-backend'} runId={run.run_id} onRestored={id=>{void refreshRun(id).catch(()=>setError('数据副本已建立，请从项目记录打开。'))}}/>}{run.source_import&&<p className="inline-state">这是导入的暂停项目，源码尚未运行或验收。点击“在此项目上继续修改”，确认产品方案后再开始开发。</p>}{run.operator_assisted&&<div className="inline-state">本次由 Codex 辅助完成 · {run.operator_note}</div>}{!isTerminal&&<button className="text-button" disabled={!!pauseNotice} onClick={pauseRun}>暂停任务</button>}{!isTerminal&&<button className="text-button" onClick={()=>void cancelRun()}>取消任务</button>}{pauseNotice&&!isTerminal&&<div>{pauseNotice}</div>}{(run.status==='completed'||(run.status==='paused'&&(run.source_import||run.local_data_restored)))&&<button className="text-button" disabled={busy} onClick={()=>{setParentRunId(run.run_id);setApplicationType(run.product_spec?.application_type??'frontend');setReferenceIds([]);setKnowledgeIds([]);setRemoteIds([]);setResearchEnabled(false);setWebSourceIds([]);setDesignId(null);setRequest('');setView('workspace');window.scrollTo({top:0,behavior:'smooth'})}}>在此项目上继续修改 →</button>}{run.parent_run_id&&<button className="text-button" onClick={()=>{void refreshRun(run.parent_run_id!)}}>查看上一版本 {run.parent_run_id.slice(0,8)}</button>}
              {run.product_spec&&<><b>{run.product_spec.product_name} · 需求摘要</b><div>{run.product_spec.summary}</div><div>目标用户：{run.product_spec.target_users?.join('、')}</div><div>页面：{run.product_spec.pages?.join('、')}</div><div>核心功能：{run.product_spec.core_features?.join('、')}</div></>}
              {run.acceptance_criteria?.length>0&&<><b>验收标准</b>{run.acceptance_criteria.map(c=><div key={c}>•　{c}</div>)}</>}
              {run.plan?.length>0&&<><b>开发计划</b>{run.plan.map((p,i)=><div key={p}>{i+1}.　{p}</div>)}</>}
              <RunCosts key={run.run_id+'-costs'} runId={run.run_id} status={run.status}/>{run.executor_tasks&&<TaskPanel key={run.run_id+'-tasks'} runId={run.run_id} tasks={run.executor_tasks} turns={run.executor_turns??0} limit={run.execution_step_limit??40} feedback={run.executor_feedback}/>}
              {!!run.knowledge_sources?.length&&<KnowledgeEvidence key={run.run_id+'-knowledge'} kind="runs" id={run.run_id}/>}
              {(run.build_status||run.test_status)&&<><b>验证状态</b><div>构建：{run.build_status==='passed'?'通过':run.build_status==='failed'?'失败':'等待中'}　·　Playwright：{run.test_status==='passed'?'通过':run.test_status==='failed'?'失败':run.test_status==='skipped'?'已跳过':'等待中'}</div></>}
              {run.last_error&&<div className="error-detail">{run.last_error}</div>}
              {run.build_output&&<details className="run-log"><summary>查看构建日志</summary><pre>{run.build_output}</pre></details>}
              {run.test_output&&<details className="run-log"><summary>查看 Playwright 测试日志</summary><pre>{run.test_output}</pre></details>}
              {run.trace?.length>0&&<><b>运行 Trace</b>{run.trace.map((item,i)=><div className="trace-row" key={item.stage+'-'+i}>·　{stageText[item.stage]??item.stage}：{item.summary}{item.estimated_cost_cny!==undefined?'（约 ¥'+item.estimated_cost_cny.toFixed(4)+'）':''}</div>)}</>}
              {(isTerminal||!!run.executor_tasks)&&<SourceViewer key={run.run_id+'-sources'} runId={run.run_id} hasDiff={!!run.artifact_paths?.git_diff}/>}{isTerminal&&<Versions runId={run.run_id} onRestored={next=>{setRun(next);setRuns(items=>[next,...items]);setParentRunId(null)}}/>}{isTerminal&&<DeliveryCenter key={run.run_id+'-delivery'} runId={run.run_id}/> }{run.build_status==='passed'&&<ProductPreview runId={run.run_id} staticPreview={!!run.artifact_paths?.preview}/>}{run.artifact_paths&&<><b>交付文件</b><div className="artifact-links"><a href={API+'/runs/'+run.run_id+'/download'}>下载源码 ZIP</a><a href={API+'/runs/'+run.run_id+'/artifact/report'}>交付报告</a><a href={API+'/runs/'+run.run_id+'/artifact/git_diff'}>Git diff</a><a href={API+'/runs/'+run.run_id+'/artifact/trace'}>Trace JSON</a>{run.artifact_paths.product_spec&&<a href={API+'/runs/'+run.run_id+'/artifact/product_spec'}>产品方案</a>}{run.artifact_paths.manifest&&<a href={API+'/runs/'+run.run_id+'/artifact/manifest'}>文件清单</a>}</div></>}
              {run.can_resume&&!busy&&<button className="text-button" onClick={resumeRun}>从 SQLite checkpoint 恢复　→</button>}
            </div>
          </>}
        </div>
        <div className="settings card"><div className="section-heading"><div><b>模型与资源</b><small>当前模型适配与本地费用保护</small></div><span className="setting-icon">✳</span></div><label>本次选择</label><div className="model-name">{capabilities?.model_provider??'deepseek'}　/　{selectedModel}</div><div className="budget"><span>模型调用</span><b>{capabilities?.model_calls_enabled?'已启用':'已关闭'}</b></div><div className="budget"><span>隔离执行环境</span><b>{capabilities?.sandbox.ready?'已就绪':'准备中'}</b></div><div className="budget"><span>累计测试预算</span><b>¥{budget?.spent_cny_estimate.toFixed(4)??'0.0000'} <i>/ ¥{budget?.budget_cny??99}</i></b></div><div className="budget"><span>已调用次数</span><b>{budget?.calls??0} <i>次</i></b></div><div className="budget-note">按高峰价估算并预留汇率缓冲；达到上限后自动阻止新调用。</div></div></section>
        <div className="footnote"><span>⌑</span> 生成代码仅挂载任务工作区　·　隔离容器禁网　·　依赖安装在固定镜像中完成</div></>:<section className="card history-page"><div className="section-heading"><div><b>{view==='projects'?'项目记录':'运行 Trace'}</b><small>{view==='projects'?'搜索、筛选与归档本机开发任务':'选择一项运行查看每个阶段的状态与模型费用'}</small></div></div>{view==='projects'?<ProjectLibrary onOpen={async id=>{await refreshRun(id);setView('workspace')}}/>:<div className="history-list">{runs.length?runs.map(item=><article className="trace-card" key={item.run_id}><header><div><b>{item.product_spec?.product_name??item.run_id.slice(0,8)}</b><small>{statusText[item.status]??stageText[item.stage]??item.stage} · {item.run_id.slice(0,8)}</small></div>{item.artifact_paths&&<a href={API+'/runs/'+item.run_id+'/artifact/trace'}>下载 Trace JSON</a>}</header><div className="trace-timeline">{item.trace?.map((event,index)=><div className="trace-entry" key={event.stage+'-'+index}><span className={'trace-mark '+event.status}>{event.status==='completed'||event.status==='passed'?'✓':'·'}</span><div><b>{event.summary}</b><small>{stageText[event.stage]??event.stage}{event.at?' · '+new Date(event.at).toLocaleString('zh-CN'):''}{event.estimated_cost_cny!==undefined?' · 约 ¥'+event.estimated_cost_cny.toFixed(4):''}</small></div></div>)}</div>{item.artifact_paths&&<div className="artifact-links"><a href={API+'/runs/'+item.run_id+'/download'}>源码 ZIP</a><a href={API+'/runs/'+item.run_id+'/artifact/report'}>交付报告</a><a href={API+'/runs/'+item.run_id+'/artifact/git_diff'}>Git diff</a></div>}</article>):<div className="empty-history">运行任务后，这里会显示完整阶段 Trace。</div>}</div>}</section>}</div>
    </main>
  </div>
}
