"""Fixed checks for confirmed static hash routes; semantic requirements stay explicit."""
import hashlib
import json
import re
from . import runner, store


def coverage(run_id):
    state = store.get_run(run_id) or {}
    routes = ['/'];pending = []
    for page in (state.get('product_spec') or {}).get('page_details', [])[:12]:
        route = page.get('route','')
        if not isinstance(route,str) or not re.fullmatch(r'/[A-Za-z0-9_\-/]*',route) or '//' in route or '..' in route:
            pending.append({'route':str(route)[:120],'reason':'参数路由或非静态路径需要实际数据与人工核对'})
            continue
        target = '/' if route=='/' else '/#'+route
        if target not in routes:routes.append(target)
    return {'routes':routes,'pending_routes':pending,
            'checks':['fresh_browser_context','root_content','uncaught_errors','horizontal_overflow','reload','keyboard_focus'],
            'manual_checks':['空数据提示是否符合需求','业务错误状态及恢复','动态路由实例','视觉与完整无障碍','逐条业务验收标准']}


def script(run_id):
    template = (runner.WORKER_CONTEXT.parent/'health.spec.ts').read_text()
    return template.replace('/* PA_ROUTES */ ["/"]', json.dumps(coverage(run_id)['routes'],ensure_ascii=False))


def fingerprint(run_id):
    return hashlib.sha256((script(run_id)+json.dumps(coverage(run_id),sort_keys=True,ensure_ascii=False)).encode()).hexdigest()
