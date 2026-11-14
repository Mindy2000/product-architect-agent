"""Independent, bounded reviewer evidence navigation; never generates verdicts."""
import json
from .context import source_index
from .text_models import role_model


def current_reads(manifest, history, episode):
    """One evidence rule for reviewer navigation and validation, including framework files."""
    reads = []
    for item in history:
        value = item.get('result') or {}
        data = value.get('data', {})
        path = data.get('path')
        expected = manifest.get('files', {}).get(path, {}).get('sha256') or manifest.get('framework', {}).get(path)
        if (item.get('operation_id','').startswith(f'reviewer_{episode}_')
                and item.get('tool_name') == 'project.read_file' and value.get('ok')
                and expected and expected == data.get('sha256') and str(data.get('content','')).strip()):
            reads.append({'path':path, 'sha256':expected, 'offset':data.get('offset'),
                          'returned_characters':len(data.get('content','')),
                          'total_characters':data.get('total_characters'),
                          'notice':'仅已返回片段，不表示整份文件或业务行为均已核实'})
    return reads


def augment(common, state, files, history):
    result=dict(common)
    # Task-completion claims are not review evidence. Keep requirements, objective
    # tool outputs and prior unresolved issues for later repair rounds separately.
    result.pop('plan_coverage',None)
    if not state.get('repair_round',0):result.pop('previous_issues',None)
    read=[]
    for item in history:
        data=(item.get('result') or {}).get('data',{})
        path=data.get('path')
        expected=common['manifest'].get('files',{}).get(path,{})
        if item['tool_name']=='project.read_file' and (item.get('result') or {}).get('ok') and expected.get('sha256')==data.get('sha256'):
            read.append({'path':path,'sha256':data.get('sha256'),'offset':data.get('offset'),'notice':'仅已返回片段；不表示整份文件已审查'})
    criteria=[]
    for index,text in enumerate(state['acceptance_criteria'][:64]):
        hints=source_index(files,{'title':text},budget=500)
        criteria.append({'index':index,'criterion':text,'candidate_files':[v['path'] for v in hints['files'][:3]],'candidate_kind':'lexical_only'})
    result['review_packet']={'version':2,'model':role_model(state,'reviewer'),
        'independent_from_developer_claims':True,'criteria':criteria,'recent_current_source_reads':read,
        'test_files':[p for p in sorted(files) if p.startswith('tests/')][:24],
        'questions':['尝试空值、重复值及边界输入','检查刷新后的数据与状态','确认测试检查了结果而非仅挂载','列出无法由当前证据确认的标准'],
        'notice':'候选导航不是读取证据，未提供开发者完成结论；必须读取当前源码并逐条核对，技术日志不等于业务通过。'}
    from .action_protocol import enabled
    if enabled(state):
        from .action_protocol import EMPTY_LIST_GUIDANCE
        reads = current_reads(common['manifest'], history, state.get('repair_round',0))
        result['review_packet'].update(current_source_reads=reads,
            current_read_files=sorted({item['path'] for item in reads}),
            empty_record_list_convention=EMPTY_LIST_GUIDANCE,
            feedback_details=state.get('specialist_feedback_details',{}))
    from .action_protocol import coverage_repair
    if coverage_repair(state):
        technical=common.get('technical',{})
        result['review_packet']['fixed_checks']=[
            {'id':name,'passed':bool(technical.get(key) and technical.get('environment_verified')),
             'source_digest':common['manifest'].get('digest'),
             'environment_sha256':technical.get('environment_sha256'),
             'scope':scope,'not_source_file':True}
            for name,key,scope in [('build','build_passed','仅编译，不证明业务行为'),
                                   ('page_health','health_passed','静态页面基础检查，不等同全部需求或无障碍验收')]]
    from .action_protocol import closeout_repair
    if closeout_repair(state):
        from .context import recent_results
        result['source_evidence']=retained_source_evidence(common['manifest'],history,state.get('repair_round',0))
        result['submission_contract']=submission_contract(result, state, history)
        result['recent_tools']=recent_results([item for item in history if item['tool_name']!='project.read_file'][-3:],budget=2000)
    return result


def retained_source_evidence(manifest, history, episode, budget=22000):
    """Keep bounded current read bodies, not just indices; never fetch unread source.

    Merge overlapping reads of the same revision. The retained range is explicit,
    and full_file_present is true only when all characters are in this payload.
    """
    grouped={}
    for item in history:
        value=item.get('result') or {};data=value.get('data') or {};path=data.get('path')
        expected=manifest.get('files',{}).get(path,{}).get('sha256') or manifest.get('framework',{}).get(path)
        content=data.get('content');offset=data.get('offset',0);total=data.get('total_characters')
        if (not item.get('operation_id','').startswith(f'reviewer_{episode}_')
                or item.get('tool_name')!='project.read_file' or not value.get('ok')
                or not expected or data.get('sha256')!=expected
                or not isinstance(content,str) or not content.strip()
                or type(offset) is not int or offset<0
                or type(total) is not int or total<offset+len(content)):
            continue
        group=grouped.setdefault(path,{'sha256':expected,'total':total,'parts':[]})
        if group['total']==total:group['parts'].append((offset,content))
    packet={'files':[],'omitted_files':0,'notice':'仅本轮真实读取且当前摘要匹配的正文；未呈现的部分不能当作已核实，完整正文也不证明业务验收通过。'}
    def size():return len(json.dumps(packet,ensure_ascii=False))
    # Keep application/client/style/test evidence ahead of bulky fixed backends.
    # A small first pass reserves a real excerpt for each file before any one
    # body consumes the remaining budget. No unread source is fetched here.
    order=sorted(grouped,key=lambda p:(0 if p=='src/App.tsx' else 1 if p.startswith('tests/')
                                     else 2 if p.startswith('src/') else 3 if p=='backend/schema.json' else 4,p))
    candidates=[]
    for path in order:
        group=grouped[path];merged=[]
        for start,body in sorted(group['parts'],key=lambda x:(x[0],-len(x[1]))):
            if merged and start<=merged[-1][0]+len(merged[-1][1]):
                prev,old=merged[-1];overlap=prev+len(old)-start
                # Conflicting receipts are not combined into invented evidence.
                if old[start-prev:start-prev+min(overlap,len(body))]!=body[:min(overlap,len(body))]:continue
                merged[-1]=(prev,old+body[max(0,overlap):])
            else:merged.append((start,body))
        entry={'path':path,'sha256':group['sha256'],'total_characters':group['total'],
               'segments':[],'full_file_present':False,'omitted_read_characters':sum(len(body) for _,body in merged)}
        candidates.append((entry,merged))

    def extend(entry, merged, allowance):
        remaining=allowance-sum(len(x['content']) for x in entry['segments'])
        for start,body in merged:
            if remaining<=0:break
            part=next((x for x in entry['segments'] if x['start']==start),None)
            if part is None:
                part={'start':start,'end':start,'content':''};entry['segments'].append(part)
            previous=len(part['content'])
            lo,hi=previous,min(len(body),previous+remaining)
            while lo<hi:
                mid=(lo+hi+1)//2;part.update(end=start+mid,content=body[:mid])
                if size()<=budget-32:lo=mid
                else:hi=mid-1
            part.update(end=start+lo,content=body[:lo])
            remaining-=lo-previous
            if not lo:entry['segments'].remove(part)
    for entry,merged in candidates:
        packet['files'].append(entry)
        extend(entry,merged,512)
        if not entry['segments'] or size()>budget-32:
            packet['files'].pop();packet['omitted_files']+=1
    for entry,merged in candidates:
        if entry not in packet['files']:continue
        extend(entry,merged,12000)
        retained=sum(len(x['content']) for x in entry['segments'])
        entry['omitted_read_characters']-=retained
        entry['full_file_present']=bool(len(entry['segments'])==1 and entry['segments'][0]['start']==0 and retained==entry['total_characters'])
    return packet


def submission_contract(context, state, history):
    """Explicit final-output boundary; navigation/read history is not a citation."""
    read={x['path'] for x in current_reads(context['manifest'],history,state.get('repair_round',0))}
    entries=[x for x in context.get('source_evidence',{}).get('files',[]) if any(s.get('content','').strip() for s in x.get('segments',[]))]
    present={x['path'] for x in entries} & read
    technical=context.get('technical',{})
    return {'required_indices':list(range(len(state.get('acceptance_criteria',[])))),
            'citable_files':sorted(present),
            'partial_files':[{'path':x['path'],'ranges':[[s['start'],s['end']] for s in x['segments']]} for x in entries if x['path'] in present and not x['full_file_present']],
            'read_but_omitted_files':sorted(read-present),
            'available_fixed_checks':[name for name,key in [('build','build_passed'),('page_health','health_passed')] if technical.get('environment_verified') and technical.get(key)],
            'ready_requires_current_technical_checks':all(technical.get(k) for k in ('build_passed','test_passed','health_passed','environment_verified')),
            'output_rule':'只输出一个顶层JSON对象，action为submit或stop；submit必须包含summary和review对象，criteria数组只能位于review.criteria，禁止裸数组、字符串或多个对象。',
            'evidence_rule':'supported只引用citable_files的已呈现片段；partial_files未呈现部分不能推断。read_but_omitted_files不可作为支持证据。不能把已知gap改成manual来求通过；证据不足则changes_requested/blocked或stop。',
            'stop_example':{'action':'stop','summary':'当前证据不足，保留缺口并停止，未声明审查通过。'}}


def format_feedback(remaining_steps):
    can_read=remaining_steps>2
    return {'code':'review_output_format','expected_root':'object',
            'allowed_actions':['tool','submit','stop'] if can_read else ['submit','stop'],
            **({'required_tool_fields':['action','summary','tool','arguments']} if can_read else {}),
            'required_submit_fields':['action','summary','review'],'criteria_location':'review.criteria',
            'notice':'上次响应未执行；只在原剩余步骤内纠正，不自动包裹数组或推断结论。'}
