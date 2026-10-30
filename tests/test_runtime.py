import json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from dbur_test import run_altium,load_test,TestError,parse_sdf
class RuntimeTests(unittest.TestCase):
 def test_real_fixture(self):
  d=parse_sdf('tests/fixtures/divider_ad24_4.sdf')
  w=next(w for c in d.charts for w in c.waves if w.name=='v(out)')
  self.assertEqual(w.point_count,1008)
  self.assertAlmostEqual(w.y[-1],2.5)
 def test_invalid_before_launch(self):
  with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent) as td:
   p=Path(td)/'bad.json';p.write_text(json.dumps({'name':'invalid','checks':[]}))
   with self.assertRaises(TestError) as c:load_test(p)
   self.assertEqual(c.exception.code,'INPUT_INVALID')
 def test_timeout_and_busy(self):
  with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent) as td, patch('dbur_test.tempfile.gettempdir',return_value=td),patch('dbur_test.subprocess.Popen') as launch:
   def mark_started(*args,**kwargs):
    work=next((Path(td)/'DBUR-Test').glob('*/run_*.pas')).parent
    (work/'started.txt').write_text('STARTED')
   launch.side_effect=mark_started
   data=load_test('examples/divider.json');report={}
   with self.assertRaises(TestError) as c:run_altium('X2.EXE',data,Path('.'),0.01,False,report)
   self.assertEqual(c.exception.code,'TIMEOUT')
   self.assertTrue(Path(report['artifacts']).exists())
   with self.assertRaises(TestError) as c:run_altium('X2.EXE',data,Path('.'),0.01,False,{})
   self.assertEqual(c.exception.code,'BUSY')
   self.assertEqual(launch.call_count,1)


class RunningAltiumTests(unittest.TestCase):
 def query(self, processes):
  from types import SimpleNamespace
  return patch('dbur_test.subprocess.run',return_value=SimpleNamespace(returncode=0,stdout=json.dumps(processes)))
 def test_absent_and_launcher_are_rejected(self):
  from dbur_test import require_running_altium
  for processes in [[],[{'ExecutablePath':'X2.EXE','CommandLine':'X2.EXE -RScriptingSystem:RunScriptFile(...)'}],[{'ExecutablePath':'other/X2.EXE','CommandLine':'X2.EXE'}]]:
   with self.subTest(processes=processes),self.query(processes):
    with self.assertRaises(TestError) as c:require_running_altium('X2.EXE')
    self.assertEqual(c.exception.code,'ALTIUM_NOT_RUNNING')
 def test_running(self):
  from dbur_test import require_running_altium
  with self.query({'ExecutablePath':'X2.EXE','CommandLine':'"X2.EXE"'}):
   require_running_altium('X2.EXE')
 def test_cli_does_not_launch_when_closed(self):
  import io
  from contextlib import redirect_stdout
  from dbur_test import main
  output=io.StringIO()
  with patch('sys.argv',['dbur_test.py','examples/divider.json','--altium',str(Path('dbur_test.py').resolve())]),self.query([]),patch('dbur_test.run_altium') as runner,redirect_stdout(output):
   self.assertEqual(main(),2)
  runner.assert_not_called()
  self.assertEqual(json.loads(output.getvalue())['error']['code'],'ALTIUM_NOT_RUNNING')
