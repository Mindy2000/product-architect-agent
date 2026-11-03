import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from fastapi import BackgroundTasks, HTTPException
from pydantic import ValidationError
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'apps/api'))
from app import main
from app.model import GeneratedBundle


class ApiGuardsTests(unittest.TestCase):
    def setUp(self):
        main.active_runs.clear()
        self.addCleanup(main.active_runs.clear)

    def test_duplicate_resume_schedules_only_one_execution(self):
        tasks = BackgroundTasks()
        state = {'run_id': 'test', 'status': 'interrupted', 'repair_round': 1}
        with patch.object(main, 'workflow', SimpleNamespace(get_state=lambda cfg: SimpleNamespace(next=['repair']))), patch.object(main.store, 'get_run', return_value=state), patch.object(main.store, 'update_run'), patch.object(main.store, 'set_pause_requested'):
            main.resume_run('test', tasks)
            with self.assertRaises(HTTPException) as error:
                main.resume_run('test', tasks)
            self.assertEqual(error.exception.status_code, 409)
            self.assertEqual(len(tasks.tasks), 1)

    def test_exhausted_repair_cannot_schedule_another_call(self):
        tasks = BackgroundTasks()
        with patch.object(main, 'workflow', SimpleNamespace(get_state=lambda cfg: SimpleNamespace(next=['repair']))), patch.object(main.store, 'get_run', return_value={'repair_round': 3}), patch.object(main.store, 'update_run'), patch.object(main.store, 'set_pause_requested'):
            with self.assertRaises(HTTPException):
                main.resume_run('test', tasks)
            self.assertEqual(len(tasks.tasks), 0)

    def test_equivalent_file_map_is_normalized_but_incomplete_bundle_rejected(self):
        files = {p: 'source' for p in ('src/App.tsx', 'src/main.tsx', 'src/style.css', 'tests/e2e.spec.ts')}
        bundle = GeneratedBundle.model_validate({'files': files})
        self.assertEqual(len(bundle.files), 4)
        with self.assertRaises(ValidationError):
            GeneratedBundle.model_validate({'files': {'src/App.tsx': 'source'}})

if __name__ == '__main__':
    unittest.main()

class EventStreamTests(unittest.IsolatedAsyncioTestCase):
    async def test_terminal_run_streams_final_state_and_closes(self):
        class Request:
            async def is_disconnected(self): return False
        with patch.object(main.store,'get_run',return_value={'run_id':'test','status':'completed'}):
            response=await main.run_events('test',Request())
            messages=[message async for message in response.body_iterator]
            self.assertEqual(len(messages),1)
            self.assertIn('"status": "completed"',messages[0])
