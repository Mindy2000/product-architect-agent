"""Exercise the release gate orchestration without Docker or provider requests."""
import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch, MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import offline_quality as quality


class OfflineQualityTests(unittest.TestCase):
    def test_clean_environment_disables_both_text_providers_and_vision(self):
        env = quality.offline_environment({'MODEL_API_KEY': 'secret', 'QWEN_TEXT_API_KEY': 'secret',
            'RAG_API_KEY': 'secret', 'GITHUB_TOKEN': 'secret', 'MODEL_CALLS_ENABLED': 'true',
            'QWEN_TEXT_CALLS_ENABLED': 'true', 'QWEN_TEXT_USE_VISION_KEY': 'true',
            'VISION_CALLS_ENABLED': 'true', 'PATH': '/synthetic'})
        self.assertTrue({'MODEL_API_KEY','QWEN_TEXT_API_KEY','RAG_API_KEY','GITHUB_TOKEN'}.isdisjoint(env))
        for name in ['MODEL_CALLS_ENABLED', 'QWEN_TEXT_CALLS_ENABLED', 'VISION_CALLS_ENABLED', 'QWEN_TEXT_USE_VISION_KEY']:
            self.assertEqual(env[name], 'false')
        self.assertEqual(env['PATH'], '/synthetic')

    def test_distinct_review_modes_have_distinct_evidence_and_existing_scripts(self):
        root = Path(__file__).resolve().parents[1]
        for name in quality.SCENARIOS:
            command = quality.scenario_command(name, root, Path('/synthetic-output'))
            self.assertTrue(Path(command[1]).is_file())
        for name,flag in [('review_submission','--review-submission'),('review_submission_stop','--review-submission-fail')]:
            command=quality.scenario_command(name,root,Path('/synthetic-output'))
            self.assertIn(flag,command)
            self.assertEqual(command[-2:],['--report','/synthetic-output/'+name+'.json'])

    def test_collection_excludes_private_files_nested_data_and_links(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);source=root/'source';folder=source/'outputs';folder.mkdir(parents=True);out=root/'report';out.mkdir()
            for name in ['offline-fixture.json','offline-screen.png','offline-private.sqlite3','secret.json']:
                (folder/name).write_text('synthetic')
            (folder/'offline-link.json').symlink_to(folder/'secret.json')
            self.assertEqual(set(quality.collect_evidence(source,out)),{'offline-fixture.json','offline-screen.png'})

    def test_interrupted_gate_records_failure_instead_of_partial_pass(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);(root/'apps/web/dist').mkdir(parents=True);(root/'apps/web/dist/index.html').write_text('fixture')
            output=root/'evidence'
            def extract(_archive,source):
                source.mkdir();(source/'.env.example').write_text('MODEL_CALLS_ENABLED=false\n')
            probe=MagicMock();probe.__enter__.return_value.connect_ex.return_value=1
            with patch.object(quality,'ROOT',root), patch.object(quality.distribution,'build',return_value=root/'fixture.zip'), patch.object(quality.distribution,'extract',side_effect=extract), patch.object(quality.socket,'socket',return_value=probe), patch.object(quality.subprocess,'run',side_effect=[subprocess.CompletedProcess([],0),subprocess.TimeoutExpired([],900)]), patch.object(sys,'argv',['offline_quality','--output',str(output)]), patch.object(quality,'EXPLICIT_REPORTS',set()), contextlib.redirect_stdout(io.StringIO()):
                with self.assertRaises(SystemExit):quality.run()
            report=json.loads((output/'report.json').read_text())
            self.assertFalse(report['passed']);self.assertEqual(len(report['checks']),2)
            self.assertTrue(report['checks'][0]['passed']);self.assertFalse(report['checks'][1]['passed'])
            self.assertIn('900',report['checks'][1]['error'])
            self.assertGreater(len(report['selected']),len(report['checks']))

    def test_existing_evidence_cannot_be_reused_as_a_pass(self):
        with tempfile.TemporaryDirectory() as temporary:
            output=Path(temporary);(output/'report.json').write_text('{"passed":true}')
            with patch.object(sys,'argv',['offline_quality','--output',str(output)]),contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit):quality.run()
            self.assertEqual((output/'report.json').read_text(),'{"passed":true}')
