"""Portable process/lock invariants, including a parent that exits before its child."""
import io,os,subprocess,sys,tempfile,time,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'apps/api'))
import psutil
from app.process_control import BoundedLineReader,OutputLimitExceeded,run_bounded
from app.platform_support import exclusive_file

class PortableTests(unittest.TestCase):
 def test_line_reader_rejects_unbounded_frame(self):
  reader=BoundedLineReader(io.StringIO('x'*100),limit=20)
  try:
   with self.assertRaises(OutputLimitExceeded):reader.line(1)
  finally:reader.close()
 def test_line_reader_reports_eof_and_preserves_unicode(self):
  reader=BoundedLineReader(io.StringIO('合成预览\n'))
  try:
   self.assertEqual(reader.line(1),'合成预览\n')
   with self.assertRaises(EOFError):reader.line(1)
  finally:reader.close()
 def test_file_lock_is_exclusive_and_released(self):
  with tempfile.TemporaryDirectory() as tmp:
   path=Path(tmp)/'worker.lock'
   with path.open('a+b') as first,path.open('a+b') as second:
    with exclusive_file(first):
     with self.assertRaises(TimeoutError):
      with exclusive_file(second,timeout=.03):pass
    with exclusive_file(second,timeout=.2):pass
 def test_deadline_kills_descendant_after_parent_exit(self):
  with tempfile.TemporaryDirectory() as tmp:
   pidfile=Path(tmp)/'pid';program='import subprocess,sys;from pathlib import Path;p=subprocess.Popen([sys.executable,"-c","import time;time.sleep(30)"]);Path(sys.argv[1]).write_text(str(p.pid))'
   with self.assertRaises(subprocess.TimeoutExpired):run_bounded([sys.executable,'-c',program,str(pidfile)],timeout=.4)
   pid=int(pidfile.read_text());deadline=time.monotonic()+2
   while psutil.pid_exists(pid) and psutil.Process(pid).status()!=psutil.STATUS_ZOMBIE and time.monotonic()<deadline:time.sleep(.05)
   self.assertTrue(not psutil.pid_exists(pid) or psutil.Process(pid).status()==psutil.STATUS_ZOMBIE)

if __name__=='__main__':unittest.main()
