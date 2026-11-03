import {useEffect,useState,useRef} from 'react'
const API='http://127.0.0.1:8000/api/v1'
type Frame={image:string;width:number;height:number}
export default function ProductPreview({runId,staticPreview}:{runId:string;staticPreview?:boolean}){
 const ownedRun=useRef<string|null>(null)
 const [frame,setFrame]=useState<Frame|null>(null)
 const [busy,setBusy]=useState(false),[error,setError]=useState(''),[text,setText]=useState('')
 useEffect(()=>{setFrame(null);setError('');return()=>{if(ownedRun.current===runId){ownedRun.current=null;void fetch(`${API}/runs/${runId}/preview/close`,{method:'POST'}).catch(()=>{})}}},[runId])
 async function send(path:string,body?:object){
  if(busy)return
  setBusy(true);setError('')
  try{const r=await fetch(`${API}/runs/${runId}/preview/${path}`,{method:'POST',...(body?{headers:{'Content-Type':'application/json'},body:JSON.stringify(body)}:{})});const d=await r.json();if(!r.ok)throw new Error(d.detail??'预览操作失败');if(path==='close'){ownedRun.current=null;setFrame(null)}else{if(path==='open')ownedRun.current=runId;setFrame(d)}}catch(e){setError(e instanceof Error?e.message:'预览操作失败')}finally{setBusy(false)}
 }
 return <section className="interactive-preview"><b>产品预览</b><p>预览中的点击和输入在隔离浏览器内执行。点击页面控件后，可在下方输入文字。</p>{!frame?<><button className="text-button" disabled={busy} onClick={()=>void send('open')}>{busy?'正在打开预览…':'打开可交互预览 →'}</button>{staticPreview&&<img className="product-preview" src={`${API}/runs/${runId}/artifact/preview`} alt="生成产品的隔离页面截图"/>}</>:<><div className="preview-tools"><input aria-label="输入到预览控件的文字" value={text} onChange={e=>setText(e.target.value)} placeholder="先点击页面中的输入框，再在这里输入文字"/><button disabled={busy||!text} onClick={()=>{void send('action',{action:'type',text});setText('')}}>输入</button>{['Tab','Backspace','Enter'].map(key=><button disabled={busy} key={key} onClick={()=>void send('action',{action:'key',key})}>{key==='Backspace'?'删除字符':key}</button>)}<button disabled={busy} onClick={()=>void send('action',{action:'scroll',y:500})}>向下滚动</button><button disabled={busy} onClick={()=>void send('action',{action:'scroll',y:-500})}>向上滚动</button><button disabled={busy} onClick={()=>void send('action',{action:'reload'})}>刷新</button><button disabled={busy} onClick={()=>void send('close')}>关闭预览</button></div><div aria-live="polite">{busy?'正在执行预览操作…':'点击下方页面进行操作'}</div><img className="product-preview interactive" src={'data:image/png;base64,'+frame.image} alt="可点击的隔离产品预览" onClick={e=>{const box=e.currentTarget.getBoundingClientRect();void send('action',{action:'click',x:Math.round((e.clientX-box.left)/box.width*frame.width),y:Math.round((e.clientY-box.top)/box.height*frame.height)})}}/></>}{error&&<div className="inline-state error">{error}</div>}</section>
}
