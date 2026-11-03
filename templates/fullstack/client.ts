// System-owned client: only this application's same-origin CRUD routes.
export type RecordValue=string|number|boolean|null
export type Row={id:string;created_at:string;updated_at:string}&Record<string,RecordValue>
function entityPath(name:string,id?:string){if(!/^[a-z][a-z0-9_]{0,39}$/.test(name))throw new Error('数据表名不合法');return '/api/data/'+name+(id?'/'+encodeURIComponent(id):'')}
async function send<T>(url:string,method='GET',body?:Record<string,RecordValue>):Promise<T>{
 const response=await fetch(url,{method,credentials:'omit',headers:body?{'Content-Type':'application/json'}:{},body:body?JSON.stringify(body):undefined})
 if(!response.ok){const value=await response.json().catch(()=>null);throw new Error(typeof value?.detail==='string'?value.detail:'数据请求未完成')}
 return response.status===204?undefined as T:await response.json() as T
}
export const api={
 list:(name:string,limit=50,offset=0)=>send<{items:Row[];total:number}>(entityPath(name)+'?limit='+Math.min(100,Math.max(1,limit))+'&offset='+Math.max(0,offset)),
 read:(name:string,id:string)=>send<Row>(entityPath(name,id)),
 create:(name:string,values:Record<string,RecordValue>)=>send<Row>(entityPath(name),'POST',values),
 update:(name:string,id:string,values:Record<string,RecordValue>)=>send<Row>(entityPath(name,id),'PATCH',values),
 remove:(name:string,id:string)=>send<void>(entityPath(name,id),'DELETE')
}

export type LocalFile={id:string;name:string;mime:string;bytes:number;sha256:string;status:string;created_at:number}
export const files={
 list:(offset=0,limit=50)=>send<{items:LocalFile[];total:number}>('/api/files?offset='+Math.max(0,offset)+'&limit='+Math.min(100,Math.max(1,limit))),
 upload:async(file:File):Promise<LocalFile>=>{const response=await fetch('/api/files?filename='+encodeURIComponent(file.name),{method:'POST',body:file,credentials:'omit'});const value=await response.json();if(!response.ok)throw new Error(typeof value.detail==='string'?value.detail:'文件上传未完成');return value as LocalFile},
 downloadUrl:(id:string)=>{if(!/^[0-9a-f-]{36}$/.test(id))throw new Error('文件标识不合法');return '/api/files/'+id+'/download'},
 remove:(id:string)=>send<void>('/api/files/'+encodeURIComponent(id),'DELETE'),
 storage:()=>send<{file_bytes:number;file_count:number;pending_count:number;limits:{file_bytes:number;total_file_bytes:number;files:number}}>('/api/storage')
}
