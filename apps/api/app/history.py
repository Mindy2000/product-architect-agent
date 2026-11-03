"""Local project catalogue. Archiving only changes catalogue metadata."""
import json
import re
import posixpath
import hashlib
import io
import zipfile
from datetime import datetime, timezone
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from fastapi.responses import StreamingResponse

from . import store
from .model import budget_status, budget_breakdown

router = APIRouter(prefix='/api/v1/projects', tags=['projects'])
FINISHED = {'completed','failed_validation','failed','failed_model','failed_sandbox',
            'blocked_budget','blocked_configuration','interrupted','paused','cancelled'}
RunStatus = Literal['all','completed','failed_validation','failed','failed_model','failed_sandbox',
                    'blocked_budget','blocked_configuration','interrupted','paused','cancelled','queued','running']


def catalogue(connection):
    connection.execute('CREATE TABLE IF NOT EXISTS project_catalogue (run_id TEXT PRIMARY KEY, archived INTEGER NOT NULL DEFAULT 0)')


@router.get('')
def projects(q: str = Query(default='', max_length=200), status: RunStatus = 'all',
             archive: Literal['active','archived','all'] = 'active',
             offset: int = Query(default=0, ge=0), limit: int = Query(default=12, ge=1, le=50)):
    conditions, params = [], []
    if archive != 'all':
        conditions.append('COALESCE(c.archived,0)=?')
        params.append(int(archive == 'archived'))
    if status != 'all':
        conditions.append("json_extract(r.state,'$.status')=?")
        params.append(status)
    if q.strip():
        conditions.append("(instr(lower(r.id),lower(?))>0 OR instr(lower(r.request),lower(?))>0 OR instr(lower(COALESCE(json_extract(r.state,'$.product_spec.product_name'),'')),lower(?))>0)")
        params.extend([q.strip()] * 3)
    where = ' WHERE ' + ' AND '.join(conditions) if conditions else ''
    source = ' FROM runs r LEFT JOIN project_catalogue c ON c.run_id=r.id'
    with store.db_connection() as connection:
        catalogue(connection)
        connection.execute('BEGIN')
        total = connection.execute('SELECT COUNT(*)' + source + where, params).fetchone()[0]
        # Return catalogue metadata, never full requests, document snapshots or tool output.
        rows = connection.execute("SELECT r.id,r.created_at,COALESCE(c.archived,0) AS archived,"
            "json_extract(r.state,'$.status') AS status,json_extract(r.state,'$.stage') AS stage,"
            "json_extract(r.state,'$.product_spec.product_name') AS title,"
            "substr(r.request,1,200) AS request_excerpt,json_extract(r.state,'$.parent_run_id') AS parent_run_id,"
            "json_extract(r.state,'$.operator_assisted') AS operator_assisted"
            + source + where + ' ORDER BY r.created_at DESC,r.id DESC LIMIT ? OFFSET ?', [*params,limit,offset]).fetchall()
    return {'items':[dict(row) for row in rows], 'total':total, 'offset':offset, 'limit':limit}


class ArchiveRequest(BaseModel):
    archived: bool
    model_config = {'extra':'forbid'}


@router.put('/{run_id}/archive')
def archive_project(run_id: UUID, payload: ArchiveRequest):
    with store.db_connection() as connection:
        catalogue(connection)
        connection.execute('BEGIN IMMEDIATE')
        row = connection.execute('SELECT state FROM runs WHERE id=?',(str(run_id),)).fetchone()
        if not row:
            raise HTTPException(404, '项目不存在。')
        if payload.archived and json.loads(row['state']).get('status') not in FINISHED:
            raise HTTPException(409, '请先暂停正在执行的任务，再归档。')
        connection.execute('INSERT INTO project_catalogue VALUES (?,?) ON CONFLICT(run_id) DO UPDATE SET archived=excluded.archived',
                           (str(run_id), int(payload.archived)))
    return {'run_id':str(run_id), 'archived':payload.archived}


@router.get('/{run_id}/costs')
def project_costs(run_id: UUID):
    if not store.get_run(str(run_id)):
        raise HTTPException(404, '项目不存在。')
    return {'budget':budget_status(str(run_id)), **budget_breakdown(str(run_id))}


def project_tree(identifier: str):
    """One bounded project tree shared by display and backup, including sibling branches."""
    current=store.get_run(identifier)
    if not current:raise HTTPException(404,'项目不存在。')
    root=current;seen={identifier};warnings=[]
    while root.get('parent_run_id'):
        parent_id=root['parent_run_id']
        if parent_id in seen:raise HTTPException(409,'版本关系出现循环，请先修复项目记录。')
        if len(seen)>=256:raise HTTPException(413,'项目树超过256个版本限制。')
        seen.add(parent_id);parent=store.get_run(parent_id)
        if not parent:
            warnings.append('父版本记录缺失：'+parent_id);break
        root=parent
    queue=[(root,0)];result=[];visited=set()
    with store.db_connection() as connection:
        while queue:
            state,depth=queue.pop(0);version_id=state['run_id']
            if version_id in visited:raise HTTPException(409,'版本关系出现重复或循环。')
            visited.add(version_id)
            if len(visited)>256:raise HTTPException(413,'项目树超过256个版本限制。')
            result.append((state,depth))
            children=connection.execute("SELECT state FROM runs WHERE json_extract(state,'$.parent_run_id')=? ORDER BY created_at,id LIMIT 257",(version_id,)).fetchall()
            if len(children)+len(queue)+len(result)>256:raise HTTPException(413,'项目树超过256个版本限制。')
            queue.extend((json.loads(row['state']),depth+1) for row in children)
    return result,warnings


@router.get('/{run_id}/delivery')
def project_delivery(run_id: UUID):
    related,warnings=project_tree(str(run_id));items=[]
    for state,depth in related:
        paths=state.get('artifact_paths') or {}
        items.append({'run_id':state['run_id'],'parent_run_id':state.get('parent_run_id'),'depth':depth,
            'created_at':state.get('created_at'),'status':state.get('status'),
            'title':state.get('product_spec',{}).get('product_name'),
            'operator_assisted':bool(state.get('operator_assisted')),
            'build_status':state.get('build_status'),'test_status':state.get('test_status'),
            'artifacts':{key:key in paths for key in ('source_zip','product_spec','report','trace','git_diff','manifest','preview')}})
    return {'root_run_id':items[0]['run_id'],'items':items,'warnings':warnings}


@router.get('/{run_id}/impact')
def project_impact(run_id: UUID):
    """Deterministic source delta and explicitly heuristic page attribution."""
    identifier=str(run_id)
    current=store.get_run(identifier)
    if not current:raise HTTPException(404,'项目不存在。')
    parent_id=current.get('parent_run_id')
    if not parent_id:raise HTTPException(409,'项目起点没有父版本可供比较。')
    parent=store.get_run(parent_id)
    if not parent:raise HTTPException(404,'父版本不存在。')
    from . import runner
    try:
        before=runner.read_generated_sources(parent_id)
        after=runner.read_generated_sources(identifier)
    except runner.SandboxError as error:
        raise HTTPException(409,str(error)) from error
    added=sorted(set(after)-set(before));removed=sorted(set(before)-set(after))
    changed=sorted(name for name in set(before)&set(after) if hashlib.sha256(before[name].encode()).digest()!=hashlib.sha256(after[name].encode()).digest())
    touched=set(added+changed+removed)
    spec=current.get('product_spec') or parent.get('product_spec') or {}
    affected=set(touched)
    # Reverse local-import closure. This is static evidence, not execution analysis.
    merged={**before,**after}
    expanded=True
    while expanded:
        expanded=False
        for name,content in merged.items():
            if name in affected:continue
            imports=re.findall(r'(?:from\s*|import\s*\(?\s*)[\'"]([.][^\'"]+)[\'"]',content)
            for imported in imports:
                target=posixpath.normpath(posixpath.join(posixpath.dirname(name),imported))
                candidates={target,target+'.ts',target+'.tsx',target+'.css',target+'/index.ts',target+'/index.tsx'}
                if candidates&affected:
                    affected.add(name);expanded=True;break
    impacts=[]
    for page in spec.get('page_details',[]):
        route=page.get('route','')
        # A URL is not a source path. Only show literal route references as candidates.
        matched=[name for name in sorted(affected) if route and route!='/' and any(quote+route+quote in after.get(name,before.get(name,'')) for quote in ("'",'"'))]
        impacts.append({'page':page.get('name') or route,'path':route,'files':matched,
            'basis':'源码包含路由字面量，候选关联待复核' if matched else '暂无显式源码映射，不能判断影响'})
    criteria=current.get('acceptance_criteria') or parent.get('acceptance_criteria') or []
    return {'run_id':identifier,'parent_run_id':parent_id,'change_request':current.get('change_request') or current.get('user_request',''),
        'files':{'added':added,'modified':sorted(name for name in set(before)&set(after) if before[name]!=after[name]),'deleted':removed},'changed_test_files':sorted(name for name in touched if name.startswith('tests/')),
        'import_affected_files':sorted(affected-touched),'page_impacts':impacts,'acceptance_criteria':criteria,
        'acceptance_note':'完整验收标准一并列出供复核；系统未判断每条标准是否受影响。',
        'scope_note':'文件差异按父子源码逐字节比较；页面候选按路由字面量匹配，不代表完整依赖或行为分析。请人工确认对行为的实际影响。'}


@router.post('/{run_id}/backup')
def backup_project(run_id: UUID):
    """Build a portable project archive from approved local artifacts and Git snapshots."""
    identifier=str(run_id)
    current=store.get_run(identifier)
    if not current:
        raise HTTPException(404,'项目不存在。')
    if current.get('status') not in FINISHED:
        raise HTTPException(409,'请先暂停或结束任务，再创建备份。')
    tree,tree_warnings=project_tree(identifier)
    versions=[state for state,_ in tree]
    if any(state.get('status') not in FINISHED for state in versions):raise HTTPException(409,'项目树中有运行中的版本，请先暂停后备份。')
    if len(versions)>64:raise HTTPException(413,'版本数量超过单个备份允许的64个。')
    from . import runner
    files:dict[str,bytes]={}
    warnings=list(tree_warnings)
    total=0;max_total=64*1024*1024
    for state in versions:
        version_id=state['run_id'];prefix=f'versions/{version_id}/'
        paths=state.get('artifact_paths') or {}
        for kind,relative in paths.items():
            if kind not in {'source_zip','product_spec','report','trace','git_diff','manifest','preview'}:continue
            original=runner.ROOT/relative
            candidate=original.resolve()
            if original.is_symlink() or runner.OUTPUT_ROOT.resolve() not in candidate.parents or not candidate.is_file():
                raise HTTPException(409,'备份不完整：登记的交付文件缺失或路径异常（'+version_id+' / '+kind+'）。')
            if candidate.stat().st_size>max_total-total:raise HTTPException(413,'备份内容超过64MiB。')
            content=candidate.read_bytes()
            total+=len(content)
            if total>max_total:raise HTTPException(413,'交付文件超过单个备份允许的64MiB。')
            files[prefix+candidate.name]=content
        try:
            snapshots=runner.version_snapshots(version_id)
            if len(snapshots)>256:raise HTTPException(413,'单个版本的源码快照超过备份限制。')
            if not snapshots:warnings.append(version_id+'：没有可恢复的Git源码快照')
            for snapshot in snapshots:
                source=runner.read_source_snapshot(version_id,snapshot['commit'])
                for item in source:
                    name=prefix+'git/'+snapshot['commit']+'/'+item['path']
                    content=item['content'].encode('utf-8');total+=len(content)
                    if total>max_total:raise HTTPException(413,'源码快照超过单个备份允许的64MiB。')
                    files[name]=content
                metadata=json.dumps(snapshot,ensure_ascii=False,indent=2).encode()
                total+=len(metadata)
                if total>max_total:raise HTTPException(413,'备份内容超过64MiB。')
                files[prefix+'git/'+snapshot['commit']+'/VERSION.json']=metadata
        except (runner.SandboxError,OSError) as error:
            raise HTTPException(409,'备份不完整：无法读取版本源码快照（'+version_id+'）。') from error
    readme=('Product Architect Agent 本地项目备份\n'
        f'项目起点：{versions[0]["run_id"]}\n'
        f'包含关联版本：{len(versions)}\n'
        '源码快照位于各版本目录的 git/<commit>/ 下；交付文件位于对应版本目录。\n'
        'SHA256SUMS.json 记录ZIP内每个交付文件的校验值。\n').encode()
    files['README.txt']=readme+('\n注意事项：\n'+'\n'.join(warnings) if warnings else '\n已包含所有登记的可用交付文件和源码快照。').encode()
    manifest={'format_version':1,'completeness':'partial' if warnings else 'complete','warnings':warnings,'created_at':datetime.now(timezone.utc).isoformat(),
        'root_run_id':versions[0]['run_id'],'selected_run_id':identifier,
        'versions':[{'run_id':item['run_id'],'parent_run_id':item.get('parent_run_id'),'status':item.get('status'),
            'created_at':item.get('created_at'),'operator_assisted':bool(item.get('operator_assisted'))} for item in versions],
        'files':[{'path':name,'bytes':len(content),'sha256':hashlib.sha256(content).hexdigest()} for name,content in sorted(files.items())]}
    files['SHA256SUMS.json']=json.dumps(manifest,ensure_ascii=False,indent=2).encode()
    if sum(len(value) for value in files.values())>max_total:raise HTTPException(413,'含清单的备份超过64MiB。')
    output=io.BytesIO()
    with zipfile.ZipFile(output,'w',zipfile.ZIP_DEFLATED,compresslevel=6) as archive:
        for name,content in sorted(files.items()):archive.writestr(name,content)
    output.seek(0)
    filename=f'product-architect-{identifier}-backup.zip'
    return StreamingResponse(output,media_type='application/zip',headers={'Content-Disposition':f'attachment; filename="{filename}"','Cache-Control':'no-store'})
