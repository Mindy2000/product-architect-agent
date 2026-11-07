"""Deterministic, bounded project context. Never calls a model or reads outside sources."""
import json
import re


def result_excerpt(value, limit):
    raw = json.dumps(value, ensure_ascii=False)
    if len(raw)<=limit:return raw
    data = value.get('data') if isinstance(value,dict) else None
    if isinstance(data,dict):
        # Keep read offsets/hashes; logs retain their tail where failures usually appear.
        field = next((key for key in ('output','content') if isinstance(data.get(key),str)),None)
        if field:
            original = data[field]
            def render(count):
                excerpt = (original[-count:] if field=='output' else original[:count]) if count else ''
                return json.dumps({**value,'data':{**data,field:excerpt,
                    'context_excerpt':{'field':field,'omitted_characters':len(original)-count,
                                       'retained_part':'tail' if field=='output' else 'head'}}},ensure_ascii=False)
            if len(render(0))<=limit:
                low, high = 0, len(original)
                while low<high:
                    mid=(low+high+1)//2
                    if len(render(mid))<=limit:low=mid
                    else:high=mid-1
                return render(low)
    return raw[:limit]


def recent_results(history, budget=30000, per_result=12000):
    """Allocate newest first, then return chronological entries with explicit omissions."""
    retained = []
    for entry in reversed(history):
        raw = json.dumps(entry.get('result') or {}, ensure_ascii=False)
        available = max(0, min(per_result, budget))
        text = result_excerpt(entry.get('result') or {},available)
        budget -= len(text)
        retained.append({'tool': entry['tool_name'], 'task_id': entry['task_id'],
                         'result': text, 'omitted_characters': len(raw)-len(text)})
    return list(reversed(retained))


def source_index(files, task, budget=8000):
    """A lexical hint, not an authoritative dependency graph or permission grant."""
    query = ' '.join(str((task or {}).get(k, '')) for k in ('title','description'))
    terms = set(re.findall(r'[A-Za-z_][A-Za-z_0-9]{2,}', query.lower()))
    changed = set((task or {}).get('changed_paths', []))
    candidates = []
    for path, content in sorted(files.items()):
        symbols = list(dict.fromkeys(re.findall(r'\b(?:function|class|interface|type|const)\s+([A-Za-z_$][\w$]*)', content)))[:16]
        imports = list(dict.fromkeys(re.findall(r'\b(?:from|import)\s*[\'"]([^\'"\n]+)', content)))[:12]
        score = 10*(path in changed) + sum(term in (path+' '+' '.join(symbols)).lower() for term in terms)
        candidates.append((-score, path, {'path':path,'symbols':symbols,'imports':imports}))
    selected = []
    for _, _, item in sorted(candidates):
        size = len(json.dumps(item, ensure_ascii=False))
        if size > budget:continue
        selected.append(item);budget -= size
    return {'kind':'lexical_hints','files':selected,'omitted_files':len(files)-len(selected),
            'notice':'索引仅帮助定位；修改前必须读取当前文件片段和SHA256。未执行源码或推断需求已通过。'}


def task_memory(tasks, budget=2000):
    completed = [task for task in tasks if task.get('status')=='completed']
    selected = []
    for task in reversed(completed[-8:]):
        item = {'id':task['id'],'summary':str(task.get('completion_summary',task.get('title','')))[:300],
                'changed_paths':task.get('changed_paths',[])[:8]}
        size = len(json.dumps(item,ensure_ascii=False))
        if size>budget:continue
        selected.append(item);budget -= size
    return {'completed_count':len(completed),'recent_completed':list(reversed(selected)),
            'omitted_completed':len(completed)-len(selected)}
