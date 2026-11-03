import {useEffect,useState} from 'react'

type Costs={budget:{spent_cny_estimate:number;budget_cny:number;remaining_cny_estimate:number;available_for_new_calls_cny:number;calls:number};groups:{stage:string;model:string;calls:number;settled_cny:number;held_cny:number;unsettled_calls:number;input_tokens:number;output_tokens:number}[]}
const stages:Record<string,string>={requirements:'需求分析',product_design:'产品方案',planning:'开发规划',generation:'代码生成',executor:'任务执行',repair:'自动修复',diagnosis:'失败诊断',replanning:'重新规划'}
export default function RunCosts({runId,status}:{runId:string;status:string}){
 const [data,setData]=useState<Costs|null>(null),[error,setError]=useState(''),[revision,setRevision]=useState(0),[loading,setLoading]=useState(false)
 useEffect(()=>{
  const controller=new AbortController();setLoading(true);setError('')
  void fetch(`http://127.0.0.1:8000/api/v1/projects/${runId}/costs`,{signal:controller.signal}).then(async response=>{
   if(!response.ok)throw new Error('费用记录暂时无法读取。')
   const value=await response.json() as Costs;if(!controller.signal.aborted)setData(value)
  }).catch(e=>{if(!controller.signal.aborted)setError(e instanceof Error?e.message:'读取失败。')}).finally(()=>{if(!controller.signal.aborted)setLoading(false)})
  return ()=>controller.abort()
 },[runId,status,revision])
 return <details className="run-costs"><summary>本任务费用明细</summary><button className="text-button" disabled={loading} onClick={()=>setRevision(value=>value+1)}>{loading?'读取中…':'刷新费用'}</button>{error&&<p role="alert">{error}</p>}{data&&<>
  <div className="cost-totals"><span>已记账（含预留）<b>¥{data.budget.spent_cny_estimate.toFixed(4)}</b></span><span>任务上限<b>¥{data.budget.budget_cny.toFixed(2)}</b></span><span>当前可用<b>¥{data.budget.available_for_new_calls_cny.toFixed(4)}</b></span><span>调用次数<b>{data.budget.calls}</b></span></div>
  <p>本任务包含方案生成费用。当前可用额度同时受任务和全局上限约束；调用前还需预留足够额度。费用为本地估算，以供应商账单为准。</p>
  <div className="cost-table-wrap"><table><caption>按阶段与模型汇总</caption><thead><tr><th>阶段 / 模型</th><th>次数</th><th>输入 / 输出 tokens</th><th>已结算估算</th><th>未结算预留</th></tr></thead><tbody>{data.groups.map(group=><tr key={group.stage+'-'+group.model}><td>{stages[group.stage]??group.stage}<small>{group.model}</small></td><td>{group.calls}</td><td>{group.input_tokens} / {group.output_tokens}</td><td>¥{group.settled_cny.toFixed(4)}</td><td>¥{group.held_cny.toFixed(4)}{group.unsettled_calls>0&&<small>{group.unsettled_calls} 次待核对</small>}</td></tr>)}</tbody></table></div>
  {!data.groups.length&&<p>本任务尚无模型费用记录。</p>}
 </>}</details>
}
