import {useEffect,useState} from 'react'
import DeliveryCenter from './DeliveryCenter'
import BackupRecovery from './BackupRecovery'
import OperationsPanel from './OperationsPanel'
import SourceImport from './SourceImport'
import EngineeringPanel from './EngineeringPanel'

const API='http://127.0.0.1:8000/api/v1/projects'
const labels:Record<string,string>={completed:'执行完成',failed_validation:'技术检查未通过',failed:'运行失败',failed_model:'模型调用失败',failed_sandbox:'执行环境失败',blocked_budget:'预算不足',blocked_configuration:'配置待完成',interrupted:'服务中断',paused:'已暂停',queued:'排队中',running:'执行中',cancelled:'已取消'}
const finished=new Set(Object.keys(labels).filter(status=>!['queued','running'].includes(status)))
type Project={id:string;title?:string;request_excerpt:string;status:string;stage:string;created_at:string;archived:number;parent_run_id?:string;operator_assisted?:number}
type Page={items:Project[];total:number;offset:number;limit:number}

export default function ProjectLibrary({onOpen}:{onOpen:(id:string)=>Promise<void>}){
 const [query,setQuery]=useState(''),[search,setSearch]=useState(''),[status,setStatus]=useState('all'),[archive,setArchive]=useState('active'),[offset,setOffset]=useState(0)
 const [page,setPage]=useState<Page|null>(null),[loading,setLoading]=useState(true),[error,setError]=useState(''),[revision,setRevision]=useState(0),[busy,setBusy]=useState(false)
 const [deliveryId,setDeliveryId]=useState('')
 useEffect(()=>{
  const controller=new AbortController()
  setLoading(true);setError('');setPage(null)
  const params=new URLSearchParams({q:search,status,archive,offset:String(offset),limit:'12'})
  void fetch(`${API}?${params}`,{signal:controller.signal}).then(async response=>{
   if(!response.ok)throw new Error('项目列表暂时无法读取。')
   const next=await response.json() as Page
   if(!controller.signal.aborted){if(next.total>0&&offset>=next.total)setOffset(Math.floor((next.total-1)/12)*12);else setPage(next)}
  }).catch(e=>{if(!controller.signal.aborted)setError(e instanceof Error?e.message:'读取失败。')}).finally(()=>{if(!controller.signal.aborted)setLoading(false)})
  return ()=>controller.abort()
 },[search,status,archive,offset,revision])
 async function open(id:string){setBusy(true);setError('');try{await onOpen(id)}catch{setError('无法打开项目，请刷新后重试。')}finally{setBusy(false)}}
 async function toggle(item:Project){
  setBusy(true);setError('')
  try{
   const response=await fetch(`${API}/${item.id}/archive`,{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify({archived:!item.archived})})
   if(!response.ok){const body=await response.json().catch(()=>null);throw new Error(typeof body?.detail==='string'?body.detail:'归档操作失败。')}
   setRevision(value=>value+1)
  }catch(e){setError(e instanceof Error?e.message:'归档操作失败。')}finally{setBusy(false)}
 }
 return <div className="project-library"><SourceImport onImported={()=>setRevision(value=>value+1)}/><EngineeringPanel/><BackupRecovery/><OperationsPanel/>
  <form className="project-filters" onSubmit={e=>{e.preventDefault();setOffset(0);setSearch(query.trim())}}>
   <label>搜索项目<input value={query} maxLength={200} onChange={e=>setQuery(e.target.value)} placeholder="项目名、需求关键词或任务编号"/></label>
   <label>任务状态<select value={status} onChange={e=>{setOffset(0);setStatus(e.target.value)}}><option value="all">全部状态</option>{Object.entries(labels).map(([value,label])=><option value={value} key={value}>{label}</option>)}</select></label>
   <label>归档范围<select value={archive} onChange={e=>{setOffset(0);setArchive(e.target.value)}}><option value="active">未归档</option><option value="archived">已归档</option><option value="all">全部项目</option></select></label>
   <button type="submit" className="text-button">搜索</button><button type="button" className="text-button" disabled={loading||busy} onClick={()=>setRevision(value=>value+1)}>刷新</button>
  </form>
  <p className="catalogue-note">归档仅整理列表，保留源码、版本、费用与运行记录。继续修改会创建独立的新任务。</p>
  {error&&<p className="inline-state error" role="alert">{error}</p>}
  <div aria-live="polite">{loading?<p className="empty-history">正在读取项目…</p>:page&&<>
   <div className="history-list">{page.items.length?page.items.map(item=><article className="project-entry" key={item.id}>
    <div><span className="history-status">{labels[item.status]??item.status}</span>{!!item.archived&&<span className="tiny-tag">已归档</span>}{!!item.operator_assisted&&<span className="tiny-tag">Codex 辅助完成</span>}</div>
    <h3>{item.title||item.request_excerpt||item.id.slice(0,8)}</h3><p>{item.request_excerpt}</p>
    <small>{new Date(item.created_at).toLocaleString('zh-CN')} · {item.id.slice(0,8)}</small>
    {item.parent_run_id&&<button className="text-button" disabled={busy} onClick={()=>void open(item.parent_run_id!)}>查看上一版本 {item.parent_run_id.slice(0,8)}</button>}
    <div className="artifact-links"><button className="text-button" disabled={busy} onClick={()=>void open(item.id)}>打开项目 →</button><button className="text-button" aria-expanded={deliveryId===item.id} onClick={()=>setDeliveryId(current=>current===item.id?'':item.id)}>交付与版本</button><button className="text-button" disabled={busy||(!item.archived&&!finished.has(item.status))} onClick={()=>void toggle(item)}>{item.archived?'移出归档':'归档'}</button></div>
    {deliveryId===item.id&&<DeliveryCenter runId={item.id}/>}
   </article>):<p className="empty-history">没有符合条件的项目，可调整搜索条件或归档范围。</p>}</div>
   <div className="project-pagination"><span>共 {page.total} 项 · 第 {Math.floor(offset/12)+1} 页</span><button className="text-button" disabled={offset===0||busy} onClick={()=>setOffset(value=>Math.max(0,value-12))}>上一页</button><button className="text-button" disabled={offset+12>=page.total||busy} onClick={()=>setOffset(value=>value+12)}>下一页</button></div>
  </>}</div>
 </div>
}
