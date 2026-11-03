import {useEffect,useState} from 'react'
const API='http://127.0.0.1:8000/api/v1'
type Version={commit:string;at:string;title:string}
export default function Versions({runId,onRestored}:{runId:string;onRestored:(run:any)=>void}){
 const [versions,setVersions]=useState<Version[]>([]),[busy,setBusy]=useState(false),[error,setError]=useState('')
 useEffect(()=>{let active=true;setVersions([]);fetch(`${API}/runs/${runId}/versions`).then(r=>r.ok?r.json():[]).then(d=>{if(active)setVersions(d)}).catch(()=>{});return()=>{active=false}},[runId])
 async function restore(commit:string){if(!window.confirm('将从此源码快照创建一个暂停的新项目副本。原项目保留，不自动构建或调用模型；之后可手动继续。确认恢复吗？'))return;setBusy(true);setError('');try{const r=await fetch(`${API}/runs/${runId}/restore`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({commit})});const d=await r.json();if(!r.ok)throw new Error(d.detail??'恢复失败');onRestored(d)}catch(e){setError(e instanceof Error?e.message:'恢复失败')}finally{setBusy(false)}}
 return versions.length?<details><summary>源码版本与恢复（{versions.length}）</summary><p>恢复会创建新版本并重新构建验收，保留原项目，不调用模型。</p>{versions.map(v=><div className="version-row" key={v.commit}><span>{v.commit.slice(0,8)} · {new Date(v.at).toLocaleString('zh-CN')} · {v.title}</span><button className="text-button" disabled={busy} onClick={()=>void restore(v.commit)}>恢复为暂停副本</button></div>)}{error&&<div className="inline-state error">{error}</div>}</details>:null
}
