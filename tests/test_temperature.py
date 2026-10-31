import copy,json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from spice_sifter import (temperature_plan,generate_nsx,TemperatureInputError,execute_test,
 TestError,load_test,parse_sdf)
ROOT=Path(__file__).resolve().parents[1]
def data():return json.loads((ROOT/'examples/temperature_series.json').read_text())
class TemperatureTests(unittest.TestCase):
 def test_real_temperature_fixtures(self):
  values=[]
  for t,expected in [(-40,.7718678688455873),(25,.658645844084736),(85,.5508387135188227)]:
   doc=parse_sdf(ROOT/'tests/fixtures'/('diode_temp_'+str(t)+'_ad24_4.sdf'))
   wave=next(w for c in doc.charts for w in c.waves if w.name=='v(out)')
   self.assertAlmostEqual(wave.y[-1],expected,places=10);values.append(wave.y[-1])
  self.assertTrue(values[0]>values[1]>values[2])
 def test_single_case_fraction_comments(self):
  d=data();d.pop('temperatures_c');d['checks']=[]
  d['spice']=d['spice'].replace('* Diode temperature','* title\n* .TEMP -20\n.tEmP +25.5 ; Celsius')
  self.assertEqual(temperature_plan(d),[25.5])
  nsx=generate_nsx(d,ROOT)
  self.assertIn('.OPTIONS TEMP=+25.5',nsx);self.assertIn('* .TEMP -20',nsx)
 def test_invalid_lists(self):
  for values in [[],[25,25.0],[True],[float('nan')],[float('inf')],[-273.15],[-300],None,'25']:
   d=data();d['temperatures_c']=values
   with self.subTest(values=values),self.assertRaises(TemperatureInputError):temperature_plan(d)
 def test_conflicts(self):
  for directive in ['.TEMP 25','.OPTIONS TEMP=25','.options\n+ temp = 25']:
   d=data();d['spice']=d['spice'].replace('* Diode temperature','* title\n'+directive)
   with self.subTest(directive=directive),self.assertRaises(TemperatureInputError):temperature_plan(d)
 def test_invalid_directives(self):
  for directive in ['.TEMP -40 25 85','.TEMP NaN','.TEMP inf','.TEMP -273.15','.TEMP 25\n.TEMP 85','.TEMP 25\n.OPTIONS TEMP=25','.SUBCKT a 1 0\n.TEMP 25\n.ENDS a','.TEMP 25\n+ 85','.LIB section\n.TEMP 25\n.ENDL','.IF 1\n.TEMP 25\n.ENDIF']:
   d=data();d.pop('temperatures_c');d['checks']=[];d['spice']=d['spice'].replace('* Diode temperature','* title\n'+directive)
   with self.subTest(directive=directive),self.assertRaises(TemperatureInputError):temperature_plan(d)
 def test_tnom_untouched(self):
  d=data();d['spice']=d['spice'].replace('* Diode temperature','* title\n.OPTIONS TNOM=25')
  self.assertEqual(temperature_plan(d),[-40,25,85]);self.assertIn('.OPTIONS TNOM=25',generate_nsx(d,ROOT))
 def test_missing_check_and_graph_temperatures(self):
  for graph in [False,True]:
   d=data()
   if graph:d['output']={'graphs':[dict(name='v',type='signal',signal='v(out)',temperatures_c=[99])]}
   else:d['checks'][0]['temperature_c']=99
   with self.assertRaises(TemperatureInputError) as c:temperature_plan(d)
   self.assertEqual(c.exception.temperature_code,'TEMP_GRAPH_NOT_FOUND' if graph else 'TEMP_CHECK_NOT_FOUND')
 def test_no_temp_unchanged(self):
  d=json.loads((ROOT/'examples/divider.json').read_text())
  self.assertIsNone(temperature_plan(d));self.assertNotIn('.OPTIONS TEMP',generate_nsx(d,ROOT))
 def test_series_error_continues(self):
  d=data();seen=[]
  def runner(exe,variant,base,timeout,keep,report,verbose):
   t=report['temperature_c'];seen.append(t)
   self.assertEqual([c['temperature_c'] for c in variant['checks']],[t])
   if t==25:raise TestError('SIM_NOT_RUN','specific simulator diagnostic')
   report['simulation_status']='PASS';return 'PASS',[dict(variant['checks'][0],value=.7,passed=True)]
  report={}
  with patch('spice_sifter.run_altium',side_effect=runner),patch('spice_sifter.require_running_altium'):
   execute_test('X2',d,ROOT,30,False,False,report)
  self.assertEqual(seen,[-40,25,85]);self.assertEqual(report['status'],'ERROR')
  self.assertEqual([r['status'] for r in report['runs']],['PASS','ERROR','PASS'])
  self.assertEqual(report['runs'][1]['error']['message'],'specific simulator diagnostic')
 def test_filter_graphs_no_checks_and_fail(self):
  d=data();d['checks']=[d['checks'][1]]
  d['output']={'graphs':[dict(name='v',type='signal',signal='v(out)',temperatures_c=[-40,85],max_points=2)]}
  def runner(exe,variant,base,timeout,keep,report,verbose):
   t=report['temperature_c'];report['simulation_status']='PASS'
   temperature_plan(variant)
   if variant['output']['graphs']:report['graphs']=[dict(name='v',x=[0,1],real=[.7,.7])]
   return ('FAIL',[dict(passed=False)]) if variant['checks'] else ('PASS',[])
  r={}
  with patch('spice_sifter.run_altium',side_effect=runner),patch('spice_sifter.require_running_altium'):execute_test('X2',d,ROOT,30,False,False,r)
  self.assertEqual(r['status'],'FAIL');self.assertEqual(r['summary'],dict(total=3,passed=2,failed=1,errors=0))
  self.assertEqual(r['runs'][0]['checks_status'],'NOT_CONFIGURED')
  self.assertNotIn('graphs',r['runs'][1]);self.assertEqual(r['runs'][2]['graphs'][0]['temperature_c'],85)
 def test_single_output(self):
  d=json.loads((ROOT/'examples/temperature_single.json').read_text());r={}
  with patch('spice_sifter.run_altium',return_value=('PASS',[])),patch('spice_sifter.require_running_altium'):execute_test('X2',d,ROOT,30,False,False,r)
  self.assertEqual(r['temperature_c'],25);self.assertNotIn('runs',r)
 def test_loader_temperature_graphs_only(self):
  d=data();d['checks']=[]
  with tempfile.TemporaryDirectory(dir=ROOT/'tests') as td:
   p=Path(td)/'test.json';p.write_text(json.dumps(d))
   self.assertEqual(load_test(p)['checks'],[])
if __name__=='__main__':unittest.main()
