import React,{useState} from 'react'
import {createRoot} from 'react-dom/client'
import VisualSettings from '../../apps/web/src/VisualSettings'
import WorkbenchCenter from '../../apps/web/src/WorkbenchCenter'
import CollaborationPanel from '../../apps/web/src/CollaborationPanel'
import RuleSelection from '../../apps/web/src/RuleSelection'
import KnowledgeLibrary from '../../apps/web/src/KnowledgeLibrary'
import KnowledgeEvidence from '../../apps/web/src/KnowledgeEvidence'
import BranchEditor from '../../apps/web/src/BranchEditor'
import {apiJson} from '../../apps/web/src/api'
import '../../apps/web/src/style.css'
const rid=new URLSearchParams(location.search).get('run_id')!
function picture(w:number,h:number){const c=document.createElement('canvas');c.width=w;c.height=h;const ctx=c.getContext('2d')!;ctx.fillStyle='#f1f5f9';ctx.fillRect(0,0,w,h);ctx.fillStyle='#16324f';ctx.font='24px sans-serif';ctx.fillText('合成任务页面',40,70);return c.toDataURL('image/png').split(',')[1]}
function Test(){
const [tab,setTab]=useState('视觉'),[selected,setSelected]=useState<string[]>([])
const [frame,setFrame]=useState({image:picture(1000,720),width:1000,height:720,visual_settings:{enabled:false,viewport:'desktop'}})
const [applied,setApplied]=useState<any>(null)
const [branches,setBranches]=useState(JSON.stringify([{label:'分支甲',decisions:[{action:'read_file',path:'src/App.tsx'}]},{label:'分支乙',decisions:[{action:'stop'}]}]))
return <main style={{maxWidth:1100,margin:'20px auto',padding:16}}><nav>{['视觉','规则','资料','引用','设置','分支'].map(t=><button key={t} onClick={()=>setTab(t)}>测试：{t}</button>)}</nav>
{tab==='视觉'&&<><VisualSettings busy={false} frame={frame} apply={async value=>{const validated=await apiJson<any>('/test/visual',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(value)});const w=validated.viewport==='mobile'?390:1000,h=validated.viewport==='mobile'?844:720;setFrame({image:picture(w,h),width:w,height:h,visual_settings:validated});setApplied(validated);return true}}/><pre style={{whiteSpace:'pre-wrap',overflowWrap:'anywhere'}} aria-label="已应用设置">{JSON.stringify(applied)}</pre></>}
{tab==='规则'&&<><CollaborationPanel runId={rid} status="paused" onRefresh={()=>{}}/><RuleSelection runId={rid} onChange={()=>{}}/></>}
{tab==='资料'&&<KnowledgeLibrary selected={selected} onChange={setSelected}/>}
{tab==='引用'&&<KnowledgeEvidence kind="runs" id={rid}/>}
{tab==='设置'&&<WorkbenchCenter onBack={()=>{}}/>}
{tab==='分支'&&<section className="expansion-panel"><BranchEditor value={branches} onChange={setBranches} files={[{path:'src/App.tsx',sha256:'a'.repeat(64)}]} disabled={false}/><pre style={{whiteSpace:'pre-wrap',overflowWrap:'anywhere'}} aria-label="分支结果">{branches}</pre></section>}
</main>}
createRoot(document.getElementById('root')!).render(<Test/>);
