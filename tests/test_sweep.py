# SPDX-FileCopyrightText: 2026 nomatin
# SPDX-License-Identifier: GPL-3.0-only
import copy,json,tempfile,unittest
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch
from spice_sifter import (parameter_axes,axis_value,sweep_plan,render_parameters,condition_indices,
 SweepInputError,execute_test,TestError,load_test,main)
ROOT=Path(__file__).resolve().parents[1]
def data():return json.loads((ROOT/'examples/sweep_divider.json').read_text())
class SweepTests(unittest.TestCase):
 def test_list_and_order(self):
  axes,total=sweep_plan(data());self.assertEqual(total,6)
  self.assertEqual(list(axes),['VIN','RLOAD']);self.assertEqual(axis_value(axes['VIN'],2),Decimal(10))
 def test_linear(self):
  for spec,values in [(dict(start=0,stop=1,step=.1),['0','0.1','0.2','0.3','0.4','0.5','0.6','0.7','0.8','0.9','1']), (dict(start=1,stop=0,step=-.3),['1','.7','.4','.1']), (dict(start=2,stop=2,step=-1),['2'])]:
   d=data();d['sweep']['parameters']['VIN']=spec;d['checks']=[];d.pop('output')
   axes,_=sweep_plan(d);a=axes['VIN'];self.assertEqual([axis_value(a,j) for j in range(a['count'])],[Decimal(x) for x in values])
 def test_bad_grids(self):
  for spec,code in [({'values':[]},'SWEEP_EMPTY'),({'values':[1,1.]},'SWEEP_DUPLICATE_VALUE'),({'values':[True]},'SWEEP_INVALID'),({'values':['.END']},'SWEEP_INVALID'),({'values':[float('inf')]},'SWEEP_INVALID'),(dict(start=0,stop=5,step=0),'SWEEP_INVALID_RANGE'),(dict(start=5,stop=0,step=1),'SWEEP_INVALID_RANGE'),(dict(start=0,stop=5),'SWEEP_INVALID_RANGE'),(dict(values=[1],start=0),'SWEEP_INVALID_RANGE')]:
   d=data();d['sweep']['parameters']['VIN']=spec
   with self.subTest(spec=spec),self.assertRaises(SweepInputError) as c:sweep_plan(d)
   self.assertEqual(c.exception.sweep_code,code)
 def test_placeholders(self):
  d=data();d['spice']+='\n* {{UNKNOWN}}'
  with self.assertRaises(SweepInputError) as c:sweep_plan(d)
  self.assertEqual(c.exception.sweep_code,'SWEEP_PLACEHOLDER_MISSING')
  d=data();d['sweep']['parameters']['unused']={'values':[1]}
  with self.assertRaises(SweepInputError) as c:sweep_plan(d)
  self.assertEqual(c.exception.sweep_code,'SWEEP_PARAMETER_UNUSED')
  d=data();d.pop('sweep')
  with self.assertRaises(SweepInputError):sweep_plan(d)
 def test_case_and_names(self):
  for name in ['1VIN','bad-name','VIN X']:
   d=data();d['sweep']['parameters'][name]={'values':[1]}
   with self.assertRaises(SweepInputError):sweep_plan(d)
  d=data();d['spice']=d['spice'].replace('{{VIN}}','{{vin}}')
  with self.assertRaises(SweepInputError):sweep_plan(d)
 def test_when(self):
  d=data();d['sweep']['parameters']['VIN']=dict(start=0,stop=1,step=.1);d['checks']=[];d.pop('output')
  axes,_=sweep_plan(d)
  self.assertEqual(condition_indices({'VIN':.1+.2},axes),{'VIN':3})
  for when in [{'missing':1},{'VIN':5},{'VIN':'3'},[]]:
   with self.subTest(when=when),self.assertRaises(SweepInputError):condition_indices(when,axes)
 def test_near_values_do_not_match_twice(self):
  d=data();d['sweep']['parameters']['VIN']={'values':[1,1.0000000000001]};d['checks']=[];d.pop('output')
  axes,_=sweep_plan(d)
  self.assertEqual(condition_indices({'VIN':1},axes),{'VIN':0})
  with self.assertRaises(SweepInputError):condition_indices({'VIN':1.00000000000005},axes)
 def test_limit_before_any_launch(self):
  d=data();d['sweep']['parameters']['VIN']=dict(start=0,stop=1000,step=.1)
  with tempfile.TemporaryDirectory(dir=ROOT/'tests') as td:
   p=Path(td)/'test.json';p.write_text(json.dumps(d))
   with patch('spice_sifter.require_running_altium') as query,patch('spice_sifter.run_altium') as runner:
    with self.assertRaises(SweepInputError) as c:load_test(p)
    self.assertEqual(c.exception.sweep_code,'SWEEP_TOO_MANY_RUNS');self.assertIn('20002',c.exception.message)
    query.assert_not_called();runner.assert_not_called()
 def test_temperature_count_and_override(self):
  d=data();d['temperatures_c']=list(range(20))
  with self.assertRaises(SweepInputError):sweep_plan(d)
  self.assertEqual(sweep_plan(d,120)[1],120)
 def test_substitution_does_not_mutate(self):
  d=data();original=copy.deepcopy(d)
  self.assertIn('DC -1.25',render_parameters(d['spice'],{'VIN':Decimal('-1.25'),'RLOAD':Decimal('2000')}))
  self.assertEqual(d,original)
 def test_execution_and_graph_filters(self):
  d=data();seen=[]
  def runner(exe,variant,base,timeout,keep,report,verbose):
   p=report['parameters'];seen.append(tuple(p.values()));self.assertNotIn('{{',variant['spice'])
   report['simulation_status']='PASS'
   if variant.get('output',{}).get('graphs'):report['graphs']=[dict(name='out',x=[0,1],real=[0,1])]
   return 'PASS',[dict(c,value=2.5,passed=True) for c in variant['checks']]
  report={}
  with patch('spice_sifter.run_altium',side_effect=runner),patch('spice_sifter.require_running_altium'):execute_test('X2',d,ROOT,30,False,False,report)
  self.assertEqual(seen,[(2,1000),(2,2000),(5,1000),(5,2000),(10,1000),(10,2000)])
  self.assertEqual([r['checks_count'] for r in report['runs']],[0,0,1,0,0,0])
  self.assertEqual(['graphs' in r for r in report['runs']],[False,False,True,True,False,False])
  for r in report['runs']:
   self.assertNotIn('temperature_c',r)
   for g in r.get('graphs',[]):self.assertEqual(g['run_id'],r['run_id']);self.assertEqual(g['parameters'],r['parameters'])
 def test_temperature_is_outer_loop(self):
  d=data();d['temperatures_c']=[-40,25];seen=[]
  def runner(exe,variant,base,timeout,keep,report,verbose):
   seen.append((report['temperature_c'],report['parameters']['VIN'],report['parameters']['RLOAD']));return 'PASS',[]
  with patch('spice_sifter.run_altium',side_effect=runner),patch('spice_sifter.require_running_altium'):execute_test('X2',d,ROOT,30,False,False,{})
  self.assertEqual(seen[:2],[(-40,2,1000),(-40,2,2000)]);self.assertEqual(seen[6],(25,2,1000))
 def test_failure_continues_timeout_stops(self):
  for code,n in [('SIM_NOT_RUN',6),('TIMEOUT',2)]:
   count=[0]
   def runner(*args):
    count[0]+=1
    if count[0]==2:raise TestError(code,'specific failure')
    return 'PASS',[]
   report={}
   with patch('spice_sifter.run_altium',side_effect=runner),patch('spice_sifter.require_running_altium'):execute_test('X2',data(),ROOT,30,False,False,report)
   self.assertEqual(len(report['runs']),n);self.assertEqual(report['status'],'ERROR');self.assertEqual(report['runs'][1]['error']['message'],'specific failure')
   if code=='TIMEOUT':self.assertEqual(report['summary']['not_run'],4)
 def test_op_chart(self):
  from spice_sifter import SDFDocument,SDFChart,SDFWave,validate_document,export_graphs,generate_nsx,parse_sdf
  d=SDFDocument('1.0',[SDFChart('Operating Point',[SDFWave('v(out)','V','NodeVoltage',1,[0.],[2.5])])])
  self.assertEqual(validate_document(d,[dict(analysis='op',signal='v(out)',measurement='last',min=2.49,max=2.51)])[0],'PASS')
  self.assertEqual(export_graphs(d,[dict(name='op',type='signal',analysis='op',signal='v(out)')])[0]['x_unit'],'1')
  self.assertIn('.OP',generate_nsx({'spice':'* OP test\n.OP\n.END'},ROOT))
  actual=parse_sdf(ROOT/'tests/fixtures/op_ad24_4.sdf')
  self.assertEqual(validate_document(actual,[dict(analysis='op',signal='v(out)',measurement='last',min=.99,max=1.01)])[0],'PASS')
 def test_unresponsive_process(self):
  from types import SimpleNamespace
  from spice_sifter import require_running_altium
  result=SimpleNamespace(returncode=0,stdout=json.dumps({'ExecutablePath':'X2.EXE','CommandLine':'X2.EXE','Responding':False}))
  with patch('spice_sifter.subprocess.run',return_value=result),self.assertRaises(TestError) as c:require_running_altium('X2.EXE')
  self.assertEqual(c.exception.code,'BUSY')
if __name__=='__main__':unittest.main()
