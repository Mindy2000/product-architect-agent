import React,{useState} from 'react'
import {createRoot} from 'react-dom/client'
import RoleModelPicker,{type RoleModels} from '../../apps/web/src/RoleModels'
import WorkbenchCenter from '../../apps/web/src/WorkbenchCenter'
import ExecutionReplay from '../../apps/web/src/ExecutionReplay'
import '../../apps/web/src/style.css'
function Test(){const [tab,setTab]=useState('模型'),[roles,setRoles]=useState<RoleModels>({});const rid=new URLSearchParams(location.search).get('run_id')!
return <main style={{maxWidth:1100,margin:'20px auto',padding:16}}><nav>{['模型','设置','回放'].map(t=><button key={t} onClick={()=>setTab(t)}>测试：{t}</button>)}</nav>
{tab==='模型'&&<><RoleModelPicker value={roles} onChange={setRoles}/><pre aria-label="角色结果">{JSON.stringify(roles)}</pre></>}
{tab==='设置'&&<WorkbenchCenter onBack={()=>{}}/>}{tab==='回放'&&<ExecutionReplay runId={rid}/>}</main>}
createRoot(document.getElementById('root')!).render(<Test/>);
