"""Real compiled workbench UI, intercepted local API, synthetic data; never live ports/data."""
import json
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlparse

from fastapi import FastAPI
from fastapi.testclient import TestClient
from playwright.sync_api import sync_playwright, expect

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT/'apps/api'))
from app import workbench, evaluation, runner, real_evaluation, model

REPORT = ROOT/'outputs/improvement-batch1-ui.json'
report = {'checks': [], 'provider_calls': 0, 'live_workbench_requests': 0, 'page_errors': []}


def passed(name):
    report['checks'].append(name)
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2))
    print('PASS', name, flush=True)


with tempfile.TemporaryDirectory(prefix='pa-batch1-ui-') as temporary:
    fixture = Path(temporary)
    (fixture/'data/knowledge').mkdir(parents=True)
    document = fixture/'data/knowledge/synthetic.txt'
    document.write_text('合成资料')
    app = FastAPI(); app.include_router(workbench.router); app.include_router(evaluation.router)
    with patch.object(runner, 'ROOT', fixture), patch.object(model, '_client', side_effect=AssertionError('provider forbidden')), TestClient(app) as client, sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True, channel=os.environ.get('PA_UI_BROWSER_CHANNEL') or None)
        try:
            page = browser.new_page(viewport={'width': 1440, 'height': 1000}, accept_downloads=True)
            page.on('pageerror', lambda error: report['page_errors'].append(str(error)))
            unexpected = []
            def route(request_route):
                request = request_route.request
                url = urlparse(request.url)
                if url.netloc == '127.0.0.1:8791':
                    return request_route.continue_()
                if url.netloc != '127.0.0.1:8000':
                    unexpected.append(request.url); return request_route.abort()
                path = url.path
                actual = ['/api/v1/workbench/evidence-policy', '/api/v1/workbench/backups/plan',
                          '/api/v1/evaluations/real-protocol', '/api/v1/evaluations/real-template', '/api/v1/evaluations/real-assessment']
                if path in actual:
                    result = client.request(request.method, path, content=request.post_data_buffer,
                                            headers={'Content-Type': 'application/json'} if request.post_data_buffer else {})
                    return request_route.fulfill(status=result.status_code, headers=dict(result.headers), body=result.content)
                fixtures = {
                    '/api/v1/runs': [],
                    '/api/v1/budget': {'spent_cny_estimate': 0, 'budget_cny': 99, 'remaining_cny_estimate': 99, 'calls': 0},
                    '/api/v1/capabilities': {'model_calls_enabled': False, 'model_provider': 'deepseek', 'model': 'fixture', 'available_models': ['deepseek-flash'], 'sandbox': {'ready': False}},
                    '/api/v1/workbench/setup': {'checks': [], 'ready': False, 'completed': False, 'budget_cny': 99, 'disk_text_enabled': False, 'restart_required': False},
                    '/api/v1/workbench/backups/previews': {'items': []},
                    '/api/v1/workbench/jobs': {'items': []},
                    '/api/v1/evaluations': {'notice': '合成离线记录', 'saved_reports': [], 'offline_reports': [], 'history': []},
                    '/api/v1/templates': [], '/api/v1/references': [],
                    '/api/v1/dify/status': {'configured': False, 'collections': [], 'documents': [], 'local_credit_ceiling': 60},
                    '/api/v1/library/collections': {'items': []},
                }
                if path not in fixtures or request.method != 'GET':
                    unexpected.append(request.method+' '+path)
                    return request_route.fulfill(status=409, json={'detail': '测试未允许此操作'})
                request_route.fulfill(json=fixtures[path])
            page.route('**/*', route)
            page.goto('http://127.0.0.1:8791/')
            page.wait_for_load_state('networkidle')
            print('DOM headings:', page.get_by_role('heading').all_text_contents(), flush=True)
            expect(page.get_by_role('heading', name='工作台设置与维护')).to_be_visible()
            page.get_by_text('如何理解检查结果与当前能力', exact=True).click()
            expect(page.get_by_text('独立角色审查', exact=True)).to_be_visible()
            expect(page.get_by_text('独立上下文与权限的辅助审查；当前角色使用同一文字模型，可能存在共同偏差。', exact=True)).to_be_visible()
            passed('工作台显示统一证据等级和共同偏差说明')
            page.get_by_text('真实任务评测准备 · 尚未运行', exact=True).click()
            expect(page.get_by_text('新建任务管理', exact=True)).to_be_visible()
            expect(page.get_by_text('受控后端应用', exact=True)).to_be_visible()
            with page.expect_download() as event:
                page.get_by_role('link', name='下载空白评测结果模板').click()
            template = fixture/'template.json'; event.value.save_as(str(template))
            assert len(json.loads(template.read_text())['attempts']) == 30
            passed('五类任务与30次计划可见，下载模板不启动任务')
            upload = page.get_by_label('核对已登记的结果', exact=False)
            upload.set_input_files(str(template))
            expect(page.get_by_text('条件未完整登记；计划尚未完成，不作排名。', exact=True)).to_be_visible()
            expect(page.get_by_text('已运行 0/15；待运行 15；自主通过 0；首次通过 0；人工辅助通过 0；通过但证据不足 0。', exact=True)).to_have_count(2)
            passed('空白模板不产生成功率或完成结论')
            bad = fixture/'invalid.json'; bad.write_text('{"suite_digest":"old"}')
            upload.set_input_files(str(bad))
            expect(page.get_by_role('alert')).to_contain_text('评测记录不符合协议')
            passed('无效结果解释错误且清除旧统计')
            page.get_by_role('button', name='预览工作台备份范围').click()
            expect(page.get_by_role('button', name='下载整套工作台备份')).to_be_enabled()
            with document.open('wb') as stream: stream.truncate(129 * 1024**2)
            page.get_by_role('button', name='预览工作台备份范围').click()
            expect(page.get_by_text('当前不能创建整套备份', exact=True)).to_be_visible()
            expect(page.get_by_role('button', name='下载整套工作台备份')).to_be_disabled()
            expect(page.get_by_text('本地知识库与原始文档', exact=True)).to_be_visible()
            passed('超限仍显示占用类别，实际导出按钮禁用')
            document.write_text('恢复正常容量')
            page.get_by_role('button', name='预览工作台备份范围').click()
            expect(page.get_by_role('button', name='下载整套工作台备份')).to_be_enabled()
            passed('重新预检可从阻断恢复正常')
            page.screenshot(path=str(ROOT/'outputs/improvement-batch1-desktop.png'), full_page=True)
            page.set_viewport_size({'width': 390, 'height': 844})
            assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth + 1')
            page.screenshot(path=str(ROOT/'outputs/improvement-batch1-mobile.png'), full_page=True)
            passed('390像素页面无横向溢出')
            assert not report['page_errors'], report['page_errors']
            assert not unexpected, unexpected
            passed('无页面异常、无真实工作台或远程请求')
        finally:
            browser.close()
