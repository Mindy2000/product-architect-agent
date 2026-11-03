import sys,tempfile,unittest,uuid
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'apps/api'))
from app import runner,project

class VersionTests(unittest.TestCase):
    def test_child_starts_from_parent_without_mutating_parent(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(runner,'GENERATED_ROOT',Path(tmp)/'generated'), patch.object(runner,'ROOT',Path(tmp)):
            parent_id,child_id=str(uuid.uuid4()),str(uuid.uuid4())
            parent=runner.initialize_workspace(parent_id)
            runner.apply_generated_files(parent_id,[{'path':rel,'content':'parent-source:'+rel} for rel in runner.ALLOWED_MODEL_PATHS])
            before={rel:(parent/rel).read_bytes() for rel in runner.ALLOWED_MODEL_PATHS}
            child=runner.initialize_workspace(child_id,parent_id)
            for rel in runner.ALLOWED_MODEL_PATHS:
                self.assertEqual((child/rel).read_bytes(),before[rel])
            (child/'src/App.tsx').write_text('child-change')
            for rel in runner.ALLOWED_MODEL_PATHS:
                self.assertEqual((parent/rel).read_bytes(),before[rel])
            self.assertIn('child-change',runner._git(child,['diff']).stdout)
            self.assertFalse((child/'.git').exists())
            self.assertTrue((Path(tmp)/'data/git'/child_id).is_dir())

if __name__=='__main__':unittest.main()

class SourceBoundaryTests(unittest.TestCase):
    def test_source_reader_rejects_symlinked_source_directory(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(runner,'GENERATED_ROOT',Path(tmp)/'generated'):
            rid=str(uuid.uuid4());workspace=runner.workspace_for(rid);workspace.mkdir(parents=True)
            outside=Path(tmp)/'outside';outside.mkdir();(outside/'App.tsx').write_text('private-marker')
            (workspace/'src').symlink_to(outside,target_is_directory=True)
            with self.assertRaises(runner.SandboxError):runner.read_generated_sources(rid)
