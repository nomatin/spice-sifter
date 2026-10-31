import math,unittest
from pathlib import Path
from spice_sifter import (SDFDocument,SDFChart,SDFWave,measure_value,measurement_result,validate_measurements,
 mcurve,mcrossings,mwindow,finish_measurements,process_measurements,TestError)

def doc(x,y,name='v(out)',analysis='Transient Analysis'):
 return SDFDocument('1.0',[SDFChart(analysis,[SDFWave(name,'V','General',len(x),x,y)])])
def m(typ,**kw):return dict(id='m',type=typ,signal='v(out)',**kw)
def acdoc(x,z):
 return SDFDocument('1.0',[SDFChart('AC Analysis',[
  SDFWave('v(dut)','V','General',len(x),[complex(v) for v in x],[complex(v) for v in z],True),
  SDFWave('i(vsense)','A','General',len(x),[complex(v) for v in x],[1+0j]*len(x),True)])])
def ac(typ,**kw):return dict(id='m',type=typ,voltage='v(dut)',current='i(vsense)',current_sign=1,**kw)
class MeasurementTests(unittest.TestCase):
 def test_linear_rise_fall_at(self):
  d=doc([0,1,3,10],[0,1,3,10])
  self.assertAlmostEqual(measure_value(d,m('rise_time',low=0,high=10))[0],8)
  self.assertAlmostEqual(measure_value(doc([0,2,7,10],[10,8,3,0]),m('fall_time',low=0,high=10))[0],8)
  self.assertEqual(measure_value(d,m('at',x=2))[0],2)
  r=m('fall_time',low=0,high=10,from_percent=90,to_percent=10)
  validate_measurements(dict(spice='* t',measurements=[r]))
  self.assertEqual(measure_value(doc([0,10],[10,0]),r)[0],8)
 def test_crossing_exact_plateau_duplicates(self):
  self.assertEqual(mcrossings([0,1,2],[0,1,2],1,'rising'),[1])
  self.assertEqual(mcrossings([0,1],[0,1],0,'rising'),[0])
  self.assertEqual(mcrossings([0,1,2],[0,1,0],1),[])
  self.assertEqual(mcurve([0,1,1,2],[0,1,1,2]),([0,1,2],[0,1,2]))
  with self.assertRaises(TestError):mcurve([0,1,1,2],[0,1,2,3])
  with self.assertRaises(TestError):mcrossings([0,1,2,3],[0,1,1,2],1)
 def test_same_front_only(self):
  row=measurement_result(m('rise_time',low=0,high=10),lambda:measure_value(doc([0,1,2,3,4],[0,2,0,8,8]),m('rise_time',low=0,high=10)))
  self.assertEqual(row['status'],'ERROR')
 def test_delay_pairing(self):
  d=doc([0,1,2,3,4,5],[0,1,0,1,0,0],'v(in)')
  d.charts[0].waves.append(SDFWave('v(out)','V','General',6,[0,1,2,3,4,5],[0,0,1,0,1,0]))
  r=dict(id='d',type='delay',trigger=dict(signal='v(in)',level=.5,edge='rising'),target=dict(signal='v(out)',level=.5,edge='rising'))
  self.assertEqual(measure_value(d,r)[0],1)
  r['target']['edge']='falling'
  with self.assertRaises(TestError):measure_value(d,r)
  d.charts[0].waves[0].y=[0,1,1,1,1,1]
  self.assertEqual(measure_value(d,r)[0],2)
  separate=SDFDocument('1.0',[SDFChart('Transient Analysis',[d.charts[0].waves[0]]),SDFChart('Transient Analysis',[d.charts[0].waves[1]])])
  with self.assertRaises(TestError):measure_value(separate,r)
 def test_nonuniform_mean_window(self):
  d=doc([0,1,3],[0,1,3]);self.assertEqual(measure_value(d,m('mean'))[0],1.5)
  self.assertEqual(measure_value(d,m('mean',window=dict(start=.5,stop=2.5)))[0],1.5)
 def test_sine_rms_nonuniform(self):
  x=[(j/5000)**1.3*2*math.pi for j in range(5001)];y=[3*math.sin(v) for v in x]
  self.assertAlmostEqual(measure_value(doc(x,y),m('rms'))[0],3/math.sqrt(2),places=5)
 def test_ripple(self):
  self.assertEqual(measure_value(doc([0,1,2,3],[0,2,-2,0]),m('ripple',window=dict(start=1,stop=2)))[0],4)
 def test_settling(self):
  x=[j/1000 for j in range(10001)];d=doc(x,[1-math.exp(-v) for v in x])
  r=m('settling_time',target_value=1,tolerance_rel=.01,start=0,stop=10)
  self.assertAlmostEqual(measure_value(d,r)[0],math.log(100),places=5)
  r['stop']=1
  with self.assertRaises(TestError):measure_value(d,r)
 def test_capacitor_inductor(self):
  x=[100,1000,10000]
  for f in x:
   self.assertAlmostEqual(measure_value(acdoc(x,[1/(2j*math.pi*v*1e-6) for v in x]),ac('capacitance_series',frequency_hz=f))[0],1e-6)
   self.assertAlmostEqual(measure_value(acdoc(x,[2j*math.pi*v*.001 for v in x]),ac('inductance_series',frequency_hz=f))[0],.001)
  with self.assertRaises(TestError):measure_value(acdoc(x,[100+0j]*3),ac('capacitance_series',frequency_hz=1000))
 def test_cutoff(self):
  x=[10**(j/100) for j in range(601)];fc=1000
  d=acdoc(x,[1/(1+1j*f/fc) for f in x]);d.charts[0].waves[1].name='v(in)';d.charts[0].waves[1].unit='V'
  r=dict(id='f',type='cutoff_3db',input='v(in)',output='v(dut)',reference_frequency_hz=1,direction='high')
  self.assertAlmostEqual(measure_value(d,r)[0],fc,delta=1)
 def test_resonance(self):
  x=[100,500,1000,2000,5000];z=[1+1j*(f-1000) for f in x]
  self.assertEqual(measure_value(acdoc(x,z),ac('resonant_frequency'))[0],1000)
  with self.assertRaises(TestError):measure_value(acdoc(x,[complex(v) for v in range(1,6)]),ac('resonant_frequency'))
 def test_limits_and_independence(self):
  d=doc([0,1],[0,1]);a=m('mean',limits=dict(min=0,max=.1));b=m('mean')
  self.assertEqual(measurement_result(a,lambda:measure_value(d,a))['status'],'FAIL')
  r={};process_measurements(d,[m('at',x=2),b],r)
  self.assertEqual([v['status'] for v in r['measurements']],['ERROR','MEASURED'])
 def test_rds_on(self):
  d=doc([0],[.01],'v(drain)','Operating Point');d.charts[0].waves.append(SDFWave('i(vsense)','A','General',1,[0],[.002]))
  self.assertEqual(measure_value(d,ac('rds_on',voltage_unused=0)|{'voltage':'v(drain)'})[0],5)
 def test_family_crossing_temperatures(self):
  request=dict(id='vth',type='sweep_crossing',parameter='VGS',signal='id',level=.25,edge='rising')
  data=dict(spice='* {{VGS}}',sweep={'parameters':{'VGS':{'values':[1,2,3]}}},measurements=[request]);report=dict(status='PASS',error=None,runs=[])
  for t in [-40,85]:
   for v,y in [(1,.1),(2,.2),(3,.4)]:
    report['runs'].append(dict(run_id=str(t)+'_'+str(v),parameters={'VGS':v},temperature_c=t,_measurement_requests=[request],_sweep_samples={'vth':dict(status='MEASURED',value=y,unit='A')}))
  finish_measurements(data,report);self.assertEqual(len(report['measurements']),2)
  self.assertTrue(all(r['value']==2.25 for r in report['measurements']))
 def test_family_ambiguity(self):
  r=dict(id='s',type='sweep_crossing',parameter='X',signal='v',level=1)
  report=dict(status='PASS',error=None,runs=[dict(run_id=str(j),parameters={'X':x},_measurement_requests=[r],_sweep_samples={'s':dict(status='MEASURED',value=y,unit='V')}) for j,(x,y) in enumerate([(0,0),(1,2),(2,0)])])
  finish_measurements(dict(spice='* {{X}}',sweep={'parameters':{'X':{'values':[0,1,2]}}},measurements=[r]),report)
  self.assertEqual(report['measurements'][0]['error']['code'],'MEASUREMENT_AMBIGUOUS_CROSSING')
 def test_invalid_inputs(self):
  base=dict(spice='*t\n.TRAN 1u 1m\n.END',checks=[])
  for req in [m('mean',limits={}),m('at'),m('rise_time',low=1,high=0),m('ripple'),m('settling_time',target_value=0,start=0,stop=1,tolerance_rel=.1)]:
   with self.subTest(req=req),self.assertRaises(TestError):validate_measurements(dict(base,measurements=[req]))
 def test_nonfinite_axis_window_and_limits(self):
  for x,y in [([0,1],[0,float('nan')]),([1,0],[0,1]),([0,0],[0,1])]:
   with self.assertRaises(TestError):mcurve(x,y)
  with self.assertRaises(TestError):mwindow([0,1],[0,1],dict(start=-1,stop=1))
  row=measurement_result(m('mean',limits=dict(min=.5,max=.5)),lambda:(.5,'V',{}))
  self.assertEqual(row['status'],'PASS')
 def test_incomplete_family_fails(self):
  r=dict(id='s',type='sweep_crossing',parameter='X',signal='v',level=1)
  report=dict(status='PASS',error=None,runs=[dict(run_id=str(j),parameters={'X':x},_measurement_requests=[r],_sweep_samples={'s':dict(status='MEASURED',value=y,unit='V')}) for j,(x,y) in enumerate([(0,0),(1,2)])])
  finish_measurements(dict(spice='* {{X}}',sweep={'parameters':{'X':{'values':[0,1,2]}}},measurements=[r]),report)
  self.assertEqual(report['measurements'][0]['error']['code'],'MEASUREMENT_NOT_ENOUGH_POINTS')
 def test_duplicate_ids_and_unsupported(self):
  for requests in [[m('mean'),m('rms')],[m('not_supported')]]:
   with self.assertRaises(TestError):validate_measurements(dict(spice='*t',measurements=requests))
 def test_real_transient_fixture(self):
  from spice_sifter import parse_sdf
  d=parse_sdf(Path(__file__).parent/'fixtures/measurement_rc_ad24_4.sdf')
  value,unit,_=measure_value(d,m('rise_time',low=0,high=5,window=dict(start=.001,stop=.01)))
  self.assertAlmostEqual(value,.001*math.log(9),delta=1e-7);self.assertEqual(unit,'s')
 def test_real_resonance_fixture(self):
  from spice_sifter import parse_sdf
  d=parse_sdf(Path(__file__).parent/'fixtures/measurement_rlc_ad24_4.sdf')
  value,unit,_=measure_value(d,ac('resonant_frequency'))
  self.assertAlmostEqual(value,1/(2*math.pi*math.sqrt(.001*1e-6)),delta=50);self.assertEqual(unit,'Hz')
if __name__=='__main__':unittest.main()
