"""Batch-one regressions, synthetic files only; no model/network calls."""
import copy
import json
import shutil
import sqlite3
import sys
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'apps/api'))
from fastapi import FastAPI
from fastapi.testclient import TestClient
from app import evidence_policy, real_evaluation, evaluation, workbench_archive as archive
from app.delivery_report import render_report
from test_workbench import Fixture as WorkbenchFixture
from test_distribution import Fixture as DistributionFixture, d


class EvidenceTests(unittest.TestCase):
    def test_completed_report_retains_unconfirmed_evidence_and_redacts_logs(self):
        state = {'status': 'completed', 'model_name': 'fixture-model', 'test_status': 'passed',
                 'build_status': 'passed', 'test_output': 'Bearer private-test-secret', 'runtime_version': 2}
        evidence = {'notice': '仅当前源码', 'counts': {'passed': 0, 'total': 1, 'unverified': 1},
                    'technical': {'test_kind': 'generated'},
                    'criteria': [{'status': 'unreviewed', 'text': '刷新后数据保留'}]}
        report = '\n'.join(render_report('fixture', state, evidence, [], .5, None))
        self.assertIn('尚未核对', report)
        self.assertIn('不表示全部需求已验收', report)
        self.assertIn('fixture-model', report)
        self.assertIn('共同偏差', report)
        self.assertNotIn('private-test-secret', report)
        self.assertIn('[REDACTED]', report)
        self.assertEqual(evidence['counts']['passed'], 0)

    def test_model_missing_is_not_inferred(self):
        self.assertIn('未登记', '\n'.join(evidence_policy.report_notice({})))


class RealProtocolTests(unittest.TestCase):
    def result(self):
        result = real_evaluation.result_template()
        result['conditions'] = dict(source_revision='synthetic-commit', environment_digest='fixture-env',
            fixture_digest='fixture', checks_digest='checks', provider='offline', model='fixture', budget_per_attempt_cny=1)
        row = result['attempts'][0]
        row.update(outcome='passed', run_id='fixture-id', source_digest='fixture-source',
                   checks={c['id']: True for c in real_evaluation.CASES[0]['checks']},
                   build_passed=True, repair_rounds=0, interventions=0, seconds=2, estimated_cost_cny=.1,
                   evidence_refs=['synthetic independent check report'])
        return result

    def test_empty_template_is_pending_and_never_a_pass(self):
        result = real_evaluation.assess(real_evaluation.result_template())
        self.assertFalse(result['complete'])
        self.assertFalse(result['conditions_frozen'])
        self.assertEqual(result['provider_requests'], 0)
        self.assertEqual(result['strategies']['single']['pending'], 15)
        self.assertEqual(result['strategies']['team']['autonomous_passed'], 0)

    def test_denominator_keeps_missing_attempts(self):
        document = self.result(); document['attempts'] = document['attempts'][:1]
        before = copy.deepcopy(document)
        result = real_evaluation.assess(document)['strategies']['single']
        self.assertEqual(result['autonomous_rate_of_plan'], 1/15)
        self.assertEqual(result['first_passed'], 1)
        self.assertEqual(result['pending'], 14)
        self.assertEqual(document, before)

    def test_assistance_missing_evidence_or_excess_repairs_never_autonomous(self):
        for change in [{'interventions': 1}, {'interventions': None}, {'repair_rounds': 4},
                       {'evidence_refs': []}, {'checks': {}}, {'source_digest': ''}, {'build_passed': False}]:
            with self.subTest(change=change):
                document = self.result(); document['attempts'][0].update(change)
                result = real_evaluation.assess(document)['strategies']['single']
                self.assertEqual(result['autonomous_passed'], 0)
                if change == {'interventions': 1}: self.assertEqual(result['assisted_passed'], 1)

    def test_unfrozen_conditions_never_support_success(self):
        document = self.result(); document['conditions']['fixture_digest'] = ''
        self.assertEqual(real_evaluation.assess(document)['strategies']['single']['autonomous_passed'], 0)

    def test_failures_unknown_cost_and_interventions_remain_visible(self):
        document = self.result()
        document['attempts'][0].update(outcome='stopped', estimated_cost_cny=None, interventions=None, failure_category='budget')
        result = real_evaluation.assess(document)['strategies']['single']
        self.assertEqual(result['failures']['budget'], 1)
        self.assertEqual(result['cost_unknown_runs'], 1)
        self.assertEqual(result['unknown_intervention_runs'], 1)

    def test_duplicate_runs_and_incomplete_budget_cannot_support_comparison(self):
        document = self.result()
        document['attempts'][1].update(copy.deepcopy(document['attempts'][0]))
        document['attempts'][1]['repeat'] = 2
        with self.assertRaises(ValueError): real_evaluation.assess(document)
        document = self.result()
        document['attempts'][0]['estimated_cost_cny'] = 2
        result = real_evaluation.assess(document)
        self.assertFalse(result['comparison_ready'])
        self.assertEqual(result['strategies']['single']['over_budget_runs'], 1)

    def test_frozen_fixture_has_valid_schema_and_one_reproducible_mutation(self):
        from app.crud_schema import CrudSchema
        root = Path(__file__).resolve().parents[1]
        fixture = json.loads((root/'templates/evaluation/real-task-v1.json').read_text())
        CrudSchema.model_validate(fixture['crud_schema'])
        mutation = fixture['repair_mutation']
        source = fixture['baseline_files'][mutation['path']]
        self.assertEqual(source.count(mutation['before']), 1)
        self.assertNotEqual(source, source.replace(mutation['before'], mutation['after']))
        self.assertIn('状态筛选', fixture['independent_filter_test'])
        self.assertEqual(real_evaluation.result_template()['conditions']['fixture_digest'], real_evaluation.protocol()['fixture_digest'])

    def test_invalid_protocol_duplicate_and_nonfinite_numbers_rejected(self):
        for variant in ('duplicate', 'digest', 'nan', 'bool', 'fraction', 'check', 'overflow'):
            document = self.result()
            if variant == 'duplicate': document['attempts'][1] = copy.deepcopy(document['attempts'][0])
            if variant == 'digest': document['suite_digest'] = 'old'
            if variant == 'nan': document['attempts'][0]['seconds'] = float('nan')
            if variant == 'bool': document['attempts'][0]['interventions'] = False
            if variant == 'fraction': document['attempts'][0]['repair_rounds'] = .5
            if variant == 'check': document['attempts'][0]['checks']['invented'] = True
            if variant == 'overflow': document['attempts'][0]['seconds'] = 10**500
            with self.subTest(variant=variant), self.assertRaises(ValueError): real_evaluation.assess(document)

    def test_api_preparation_has_no_execution_route_or_provider_access(self):
        from app import model
        app = FastAPI(); app.include_router(evaluation.router)
        with patch.object(model, '_client', side_effect=AssertionError('provider forbidden')), TestClient(app) as client:
            protocol = client.get('/api/v1/evaluations/real-protocol').json()
            self.assertFalse(protocol['execution_enabled'])
            template = client.get('/api/v1/evaluations/real-template')
            self.assertIn('attachment', template.headers['content-disposition'])
            self.assertEqual(client.post('/api/v1/evaluations/real-assessment', json=template.json()).status_code, 200)
            self.assertEqual(client.post('/api/v1/evaluations/real-start', json={}).status_code, 404)


class BackupPreflightTests(WorkbenchFixture):
    def test_oversize_plan_still_reports_categories_but_export_refuses(self):
        with patch.object(archive, 'MAX_FILE', 16), patch.object(archive, 'MAX_ENTRIES', 2), patch.object(archive, 'LIMIT', 1024):
            plan = archive.plan(self.root)
            self.assertFalse(plan['can_export'])
            self.assertTrue(plan['groups'])
            self.assertEqual({b['code'] for b in plan['blockers']}, {'file_size', 'file_count', 'total_size'})
            self.assertNotIn('raw.txt', json.dumps(plan))
            self.assertNotIn(self.secret, json.dumps(plan))
            with self.assertRaises(archive.ArchiveError): self.create()

    def test_low_disk_is_explained_without_reading_document_contents(self):
        with patch.object(archive.shutil, 'disk_usage', return_value=shutil._ntuple_diskusage(100,99,1)):
            plan = archive.plan(self.root)
        self.assertIn('disk_space', {b['code'] for b in plan['blockers']})
        self.assertTrue((self.root/'data/knowledge/raw.txt').is_file())

    def test_existing_backup_is_preserved_if_destination_reused(self):
        path = self.create(); before = path.read_bytes()
        with self.assertRaises(FileExistsError): archive.create(self.root, path)
        self.assertEqual(path.read_bytes(), before)

    def test_restore_preview_includes_scope_and_rollback_effects(self):
        path = self.create()
        with self.client() as client:
            result = client.post('/api/v1/workbench/backups/preview', content=path.read_bytes()).json()
            self.assertFalse(result['effects']['automatic_execution'])
            self.assertFalse(result['effects']['credentials_restored'])
            self.assertIn('不会自动合并', result['effects']['rollback'])


class UpgradeIntegrityTests(DistributionFixture):
    def seed(self):
        directory = self.root/'data'; directory.mkdir()
        (directory/'record.txt').write_text('original')
        return directory

    def test_copy_corruption_prevents_commit(self):
        directory = self.seed(); package = self.package(); destination = self.base/'new'
        original = d.shutil.copyfile
        def corrupt(source, target, *args, **kwargs):
            result = original(source, target, *args, **kwargs)
            if Path(source) == directory/'record.txt': Path(target).write_text('corrupted')
            return result
        with patch.object(d.shutil, 'copyfile', side_effect=corrupt), self.assertRaises(d.DistributionError):
            d.upgrade(self.root, package, destination, True)
        self.assertFalse(destination.exists())
        self.assertEqual((directory/'record.txt').read_text(), 'original')
        self.assertFalse(list(self.base.glob('.pa-upgrade-*')))

    def test_source_write_during_upgrade_prevents_commit(self):
        directory = self.seed(); package = self.package(); destination = self.base/'new'
        original = d.shutil.copyfile
        def mutate(source, target, *args, **kwargs):
            result = original(source, target, *args, **kwargs)
            if Path(source) == directory/'record.txt': (directory/'new-record.txt').write_text('concurrent')
            return result
        with patch.object(d.shutil, 'copyfile', side_effect=mutate), self.assertRaises(d.DistributionError):
            d.upgrade(self.root, package, destination, True)
        self.assertFalse(destination.exists())
        self.assertEqual((directory/'new-record.txt').read_text(), 'concurrent')

    def test_successful_upgrade_records_checked_files_and_database(self):
        directory = self.seed()
        with closing(sqlite3.connect(directory/'sample.sqlite3')) as conn:
            conn.execute('CREATE TABLE sample(value TEXT)'); conn.execute("INSERT INTO sample VALUES ('kept')"); conn.commit()
        destination = self.base/'new'
        d.upgrade(self.root, self.package(), destination, True)
        receipt = json.loads((destination/'data/upgrade-receipt.json').read_text())
        self.assertTrue(receipt['integrity_checks']['source_unchanged'])
        self.assertEqual(receipt['integrity_checks']['databases_verified'], 1)

    def test_database_check_failure_preserves_old_directory(self):
        directory = self.seed()
        with closing(sqlite3.connect(directory/'sample.sqlite3')) as conn: conn.execute('CREATE TABLE sample(value TEXT)')
        destination = self.base/'new'; package = self.package()
        with patch.object(d, 'check_database', side_effect=d.DistributionError('synthetic integrity failure')):
            with self.assertRaises(d.DistributionError): d.upgrade(self.root, package, destination, True)
        self.assertFalse(destination.exists())
        self.assertTrue((directory/'sample.sqlite3').is_file())


if __name__ == '__main__':
    unittest.main()
