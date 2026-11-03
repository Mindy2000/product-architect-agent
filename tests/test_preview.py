import sys,tempfile,unittest,uuid
from pathlib import Path
from unittest.mock import patch,MagicMock
from pydantic import ValidationError
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'apps/api'))
from app import preview
from app.main import PreviewAction

class PreviewTests(unittest.TestCase):
    def test_browser_container_remains_isolated_and_stdin_stays_open(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'dist').mkdir();(root/'dist/index.html').write_text('<div/>')
            process=MagicMock()
            with patch.object(preview,'workspace_for',return_value=root),patch.object(preview.subprocess,'Popen',return_value=process) as popen,patch.object(preview.Preview,'read',return_value={'ok':True}):
                preview.Preview(str(uuid.uuid4()))
            command=popen.call_args.args[0]
            for flag in ('-i','--network=none','--read-only','--cap-drop=ALL','--user=pwuser'):
                self.assertIn(flag,command)
            mounts=[command[i+1] for i,item in enumerate(command[:-1]) if item=='--mount']
            self.assertEqual(len(mounts),2)
            self.assertFalse(any('docker.sock' in mount for mount in mounts))
    def test_remote_input_does_not_accept_commands_or_arbitrary_keys(self):
        with self.assertRaises(ValidationError):PreviewAction(action='shell',text='anything')
        with self.assertRaises(ValidationError):PreviewAction(action='key',key='Control+L')
        with self.assertRaises(ValidationError):PreviewAction(action='type',text='a'*501)
