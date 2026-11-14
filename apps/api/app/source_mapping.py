"""Bounded source association. Explicit markers remain evidence, never authority."""
import re
from .context import source_index
from .regression import dependency_graph

def associate(files,selection):
    marker=str(selection.get('source_marker') or '')[:200];text=str(selection.get('text') or '')[:120]
    graph,unresolved=dependency_graph(files);locations=[]
    from .source_ast import locate
    parsed=locate(files,selection)
    if parsed.get('available'):
        explicit=[v for v in parsed['locations'] if v['basis']=='explicit_marker']
        locations=explicit or parsed['locations'][:8]
    match=re.fullmatch(r'(src/[A-Za-z0-9_./-]+\.tsx?):([1-9][0-9]{0,5})',marker)
    if not parsed.get('available') and match and match[1] in files:
        path,line=match[1],int(match[2]);lines=files[path].splitlines()
        if line<=len(lines) and re.search(r'data-pa-source\s*=\s*[\'"]'+re.escape(marker)+r'[\'"]',lines[line-1]):
            locations.append({'path':path,'line':line,'basis':'marker_text','confidence':'标记文字候选；语法分析不可用，尚未确认是否为真实JSX属性'})
    if not locations and text:
        for path,body in files.items():
            if not path.startswith('src/'):continue
            for i,line in enumerate(body.splitlines(),1):
                if text in line:locations.append({'path':path,'line':i,'basis':'text_match','confidence':'文字匹配候选，可能重复或来自动态组件'})
                if len(locations)>=8:break
            if len(locations)>=8:break
    paths=list(dict.fromkeys(v['path'] for v in locations));related={p:{'imports':graph[p],'imported_by':[n for n,deps in graph.items() if p in deps]} for p in paths}
    return {'locations':locations,'candidates':paths,'dependencies':related,'unresolved_imports':unresolved[:20],'parser':parsed,
            'index':source_index(files,{'title':text},budget=2500),'uncertain':not locations or any(v['basis']!='explicit_marker' for v in locations),
            'notice':'仅受控源码的静态定位，不执行源码。语法分析显示组件与JSX候选；动态生成、包装组件、相同文字可能无法唯一定位。显式标记须与实际源码行一致。'+parsed.get('reason','')}
