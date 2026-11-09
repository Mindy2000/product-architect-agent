"""Bounded source association. Explicit markers remain evidence, never authority."""
import re
from .context import source_index
from .regression import dependency_graph

def associate(files,selection):
    marker=str(selection.get('source_marker') or '')[:200];text=str(selection.get('text') or '')[:120]
    graph,unresolved=dependency_graph(files);locations=[]
    match=re.fullmatch(r'(src/[A-Za-z0-9_./-]+\.tsx?):([1-9][0-9]{0,5})',marker)
    if match and match[1] in files:
        path,line=match[1],int(match[2]);lines=files[path].splitlines()
        if line<=len(lines) and re.search(r'data-pa-source\s*=\s*[\'"]'+re.escape(marker)+r'[\'"]',lines[line-1]):
            locations.append({'path':path,'line':line,'basis':'explicit_marker','confidence':'源码行与显式标记一致；仍须核对组件逻辑'})
    if not locations and text:
        for path,body in files.items():
            if not path.startswith('src/'):continue
            for i,line in enumerate(body.splitlines(),1):
                if text in line:locations.append({'path':path,'line':i,'basis':'text_match','confidence':'文字匹配候选，可能重复或来自动态组件'})
                if len(locations)>=8:break
            if len(locations)>=8:break
    paths=list(dict.fromkeys(v['path'] for v in locations));related={p:{'imports':graph[p],'imported_by':[n for n,deps in graph.items() if p in deps]} for p in paths}
    return {'locations':locations,'candidates':paths,'dependencies':related,'unresolved_imports':unresolved[:20],
            'index':source_index(files,{'title':text},budget=2500),'uncertain':not locations or any(v['basis']!='explicit_marker' for v in locations),
            'notice':'仅受控源码的静态定位；动态生成、包装组件、相同文字可能无法唯一定位。显式 data-pa-source="src/Component.tsx:行号" 必须与源码实际行一致。'}
