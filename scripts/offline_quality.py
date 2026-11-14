"""Repeatable zero-provider acceptance in a source-only disposable workspace."""
import argparse
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import time

import distribution

ROOT = Path(__file__).resolve().parents[1]
SCENARIOS = {
    'ui_workflow': ('ui_workflow', ()),
    'checkpoint_restart': ('checkpoint_restart', ()),
    'fullstack_roundtrip': ('fullstack_roundtrip', ()),
    'workbench_roundtrip': ('workbench_roundtrip', ()),
    'regression_roundtrip': ('regression_roundtrip', ()),
    'audit_quality': ('audit_quality', ()),
    'team_checkpoint': ('team_checkpoint', ()),
    'team_scenarios': ('team_scenarios', ()),
    'interactive_expansion': ('interactive_expansion', ()),
    'interface_feedback': ('interface_feedback', ()),
    'review_submission': ('team_checkpoint', ('--review-submission',)),
    'review_submission_stop': ('team_checkpoint', ('--review-submission-fail',)),
}
EXPLICIT_REPORTS = {'ui_workflow', 'workbench_roundtrip', 'interface_feedback',
                    'review_submission', 'review_submission_stop'}


def offline_environment(environment):
    clean = {k: v for k, v in environment.items()
             if not any(word in k.upper() for word in ('API_KEY', 'TOKEN', 'PASSWORD', 'SECRET'))}
    clean.update(MODEL_CALLS_ENABLED='false', QWEN_TEXT_CALLS_ENABLED='false',
                 QWEN_TEXT_USE_VISION_KEY='false', VISION_CALLS_ENABLED='false', PYTHONUTF8='1')
    return clean


def scenario_command(name, source, output):
    script, flags = SCENARIOS[name]
    command = [sys.executable, str(source / 'tests/integration' / (script + '.py')), *flags]
    if name in {'ui_workflow', 'workbench_roundtrip'}:
        command += ['--source', str(source)]
    if name in EXPLICIT_REPORTS:
        command += ['--report', str(output / (name + '.json'))]
    return command


def collect_evidence(source, output):
    # Only public synthetic reports/screenshots, never databases, keys or source state.
    collected = []
    for evidence in sorted((source / 'outputs').glob('offline-*')):
        if evidence.is_file() and not evidence.is_symlink() and evidence.suffix in {'.json', '.png'}:
            shutil.copyfile(evidence, output / evidence.name)
            collected.append(evidence.name)
    return collected


def run():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', default='outputs/offline-quality')
    parser.add_argument('--skip-workbench', action='store_true')
    parser.add_argument('--scenario', choices=SCENARIOS)
    parser.add_argument('--list', action='store_true', help='List scenarios without running them')
    args = parser.parse_args()
    if args.list:
        print('\n'.join(SCENARIOS))
        return
    selected = [name for name in SCENARIOS
                if (not args.scenario or name == args.scenario)
                and not (args.skip_workbench and name == 'workbench_roundtrip')]
    if not selected:
        parser.error('No scenarios selected')
    output = Path(args.output).resolve()
    # Reject old evidence: an interrupted/failed run cannot inherit a previous pass.
    if output.exists() and any(output.iterdir()):
        parser.error('Output directory is not empty; use a new evidence directory')
    output.mkdir(parents=True, exist_ok=True)
    for port in (8000, 5173):
        with socket.socket() as probe:
            if probe.connect_ex(('127.0.0.1', port)) == 0:
                raise SystemExit(f'Port {port} is occupied; stop your workbench before disposable acceptance.')
    environment = offline_environment(os.environ)
    results = []
    def write_summary():
        (output / 'report.json').write_text(json.dumps({
            'provider': 'deterministic_offline', 'provider_calls': 0,
            'selected': selected, 'checks': results,
            'passed': len(results) == len(selected) and all(r['passed'] for r in results),
        }, indent=2))
    write_summary()
    with tempfile.TemporaryDirectory(prefix='pa-offline-', dir=ROOT / 'data' if (ROOT / 'data').exists() else ROOT) as temporary:
        temporary = Path(temporary)
        package = distribution.build(ROOT, temporary / 'package', kind='source')
        source = temporary / 'source'
        distribution.extract(package, source)
        shutil.copytree(ROOT / 'apps/web/dist', source / 'apps/web/dist')
        shutil.copyfile(source / '.env.example', source / '.env')
        (source / 'data').mkdir()
        (source / 'outputs').mkdir()
        for name in selected:
            started = time.monotonic()
            error = None
            code = 1
            with (output / (name + '.log')).open('wb') as stream:
                try:
                    child = subprocess.run(scenario_command(name, source, output), cwd=source,
                                           env=environment, stdout=stream, stderr=subprocess.STDOUT, timeout=900)
                    code = child.returncode
                except subprocess.TimeoutExpired:
                    error = 'Scenario exceeded 900 seconds'
            evidence = collect_evidence(source, output)
            if name in EXPLICIT_REPORTS:
                report = output / (name + '.json')
                if report.is_file():
                    evidence.append(report.name)
                    try:
                        payload = json.loads(report.read_text())
                        if payload.get('passed') is False:
                            error = 'Scenario report failed'
                    except (ValueError, OSError):
                        error = 'Scenario report is unreadable'
                else:
                    error = error or 'Scenario report is missing'
            entry = {'scenario': name, 'passed': code == 0 and error is None,
                     'exit_code': code, 'seconds': round(time.monotonic() - started, 2), 'evidence': evidence}
            if error:
                entry['error'] = error
            results.append(entry)
            write_summary()
            print(json.dumps(entry), flush=True)
            if not entry['passed']:
                raise SystemExit(code or 1)


if __name__ == '__main__':
    run()
