"""No providers or real user data: current vs historical regression evidence."""
import copy,json,sys,unittest,uuid
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'apps/api'))
from app import regression,runner,project,store,workbench_jobs,runtime_identity
import test_closeout

class RegressionEvidenceTests(unittest.TestCase):
 setUp=test_closeout.AcceptanceTests.setUp
 def report(self):
  basis=regression.validation_basis(self.id)
  return {'available':True,'run_ids':{'baseline':self.id,'current':self.id},'validation_basis':{'baseline':basis,'current':copy.deepcopy(basis)},'comparison':{'regressions':[]}}
 def test_current_report_keeps_comparison_without_confirming_requirements(self):
  report=self.report();value=regression.evidence_status(report)
  self.assertTrue(value['current_evidence']);self.assertEqual(value['comparison'],report['comparison']);self.assertNotIn('current_evidence',report)
 def test_changed_source_is_stale(self):
  report=self.report();files=project.sources(self.id);files['src/style.css']+='\nbody{color:red}';project.import_sources(self.id,files)
  self.assertEqual(regression.evidence_status(report)['evidence_status'],'stale')
 def test_changed_image_is_stale(self):
  report=self.report()
  with patch.object(runtime_identity,'current',return_value={'digest':'changed','image_id':'sha256:'+'b'*64}):self.assertEqual(regression.evidence_status(report)['evidence_status'],'stale')
 def test_missing_docker_does_not_claim_current_or_run_any_phase(self):
  report=self.report()
  with patch.object(runtime_identity,'current',return_value=None),patch.object(runner,'run_phase') as run:
   self.assertEqual(regression.evidence_status(report)['evidence_status'],'unverified');run.assert_not_called()
 def test_changed_route_is_stale(self):
  report=self.report();self.state['product_spec']={'page_details':[{'route':'/settings'}]};store.update_run(self.id,self.state)
  self.assertFalse(regression.evidence_status(report)['current_evidence'])
 def test_changed_suite_is_stale(self):
  self.state['acceptance_profile']='todo-v1';store.update_run(self.id,self.state);report=self.report();path=self.root/'benchmarks/todo-v1.spec.ts';path.write_text(path.read_text()+'\n// changed')
  self.assertEqual(regression.evidence_status(report)['evidence_status'],'stale')
 def test_legacy_report_is_historical_without_runtime_calls(self):
  report={'available':True,'comparison':{'regressions':[]}}
  with patch.object(runtime_identity,'current') as inspect:
   self.assertEqual(regression.evidence_status(report)['evidence_status'],'historical');inspect.assert_not_called()
 def test_report_endpoint_does_not_rewrite_original_evidence(self):
  report=self.report();path=project.metadata_dir(self.id)/'regression-latest.json';path.write_text(json.dumps(report));original=path.read_bytes()
  with patch.object(runtime_identity,'current',return_value=None):self.assertFalse(regression.regression_result(uuid.UUID(self.id))['current_evidence'])
  self.assertEqual(path.read_bytes(),original)
 def test_maintenance_history_also_marks_stale_evidence(self):
  report=self.report();job=str(uuid.uuid4())
  connection=workbench_jobs.connect(self.root)
  try:
   connection.execute('INSERT INTO tasks VALUES (?,?,?,?,?,?,?,?,?,?,?,?,0)',(job,'regression','completed','{}',json.dumps(report),'','done',1,1,0,0,1));connection.commit()
  finally:connection.close()
  with patch.object(runtime_identity,'current',return_value=None):self.assertEqual(workbench_jobs.read(self.root,job)['result']['evidence_status'],'unverified')
