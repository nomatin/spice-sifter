import copy
import math
from pathlib import Path
import struct
import unittest
from dbur_test import (SDFDocument,SDFChart,SDFWave,TestError,parse_sdf,parse_sdf_bytes,
 validate_document,export_graphs,validate_ac_request,validate_graph_requests,generate_nsx)


def document(x=None,v=None,i=None):
 x=[100.,1000.] if x is None else x
 v=[1+0j]*len(x) if v is None else v
 i=[0.001+0j]*len(x) if i is None else i
 return SDFDocument('1.0',[SDFChart('AC Analysis',[
  SDFWave('v(dut)','V','NodeVoltage',len(x),x,v,True),
  SDFWave('i(vsense)','A','General',len(x),x[:],i,True)])])


def check(**kw):
 c=dict(analysis='ac',quantity='impedance',voltage='V(DUT)',current='I(VSENSE)',
  current_sign=1,frequency_hz=100,component='real',min=999,max=1001)
 c.update(kw);return c


def graph(**kw):
 r=dict(name='z',type='impedance',voltage='v(dut)',current='i(vsense)',
  current_sign=1,components=['real','imag','magnitude'])
 r.update(kw);return r

class ACTests(unittest.TestCase):
 def test_real_complex_fixture(self):
  doc=parse_sdf(Path(__file__).parent/'fixtures/rc_ac_ad24_4.sdf')
  c=check(frequency_hz=1000,component='imag',min=-160,max=-158)
  status,rows=validate_document(doc,[c])
  self.assertEqual(status,'PASS');self.assertAlmostEqual(rows[0]['value'],-1/(2*math.pi*1000*1e-6),places=8)
  self.assertIsInstance(doc.charts[0].waves[0].y[0],complex)
 def test_interpolate_before_division(self):
  v=[1+1j,4+2j];i=[0.1+0.2j,0.3+0.4j]
  expected=((v[0]+v[1])/2)/((i[0]+i[1])/2)
  for component,val in [('real',expected.real),('imag',expected.imag),('magnitude',abs(expected))]:
   status,rows=validate_document(document(v=v,i=i),[check(frequency_hz=550,component=component,min=val-1e-12,max=val+1e-12)])
   self.assertEqual(status,'PASS');self.assertAlmostEqual(rows[0]['value'],val)
 def test_current_sign(self):
  status,rows=validate_document(document(),[check(current_sign=-1,min=-1001,max=-999)])
  self.assertEqual(status,'PASS');self.assertEqual(rows[0]['value'],-1000)
 def test_exact_and_tolerance(self):
  for f in [100,100*(1+1e-12)]:
   self.assertEqual(validate_document(document(),[check(frequency_hz=f,frequency_sampling='exact')])[0],'PASS')
  for f,mode in [(550,'exact'),(1,'interpolate'),(1001,'interpolate')]:
   status,rows=validate_document(document(),[check(frequency_hz=f,frequency_sampling=mode)])
   self.assertEqual(rows[0]['error']['code'],'AC_FREQUENCY_OUT_OF_RANGE')
 def test_wave_errors(self):
  for mutate,code in [
   (lambda d:setattr(d.charts[0].waves[1],'x',[100,999]),'AC_FREQUENCY_GRID_MISMATCH'),
   (lambda d:setattr(d.charts[0].waves[1],'y',[0j,0j]),'IMPEDANCE_DIVISION_BY_ZERO'),
   (lambda d:setattr(d.charts[0].waves[1],'y',[1e-20+0j]*2),'IMPEDANCE_DIVISION_BY_ZERO'),
   (lambda d:setattr(d.charts[0].waves[1],'is_complex',False),'AC_COMPLEX_NOT_AVAILABLE'),
   (lambda d:setattr(d.charts[0].waves[0],'y',[complex(float('nan'),0)]*2),'NON_FINITE_RESULT'),
   (lambda d:setattr(d.charts[0].waves[0],'x',[100,100]),'AC_FREQUENCY_GRID_MISMATCH')]:
   d=document();mutate(d)
   with self.subTest(code=code):self.assertEqual(validate_document(d,[check()])[1][0]['error']['code'],code)
 def test_missing_and_ambiguous(self):
  d=document();d.charts=[]
  self.assertEqual(validate_document(d,[check()])[1][0]['error']['code'],'AC_CHART_NOT_FOUND')
  d=document();self.assertEqual(validate_document(d,[check(current='missing')])[1][0]['error']['code'],'AC_WAVE_NOT_FOUND')
  d.charts.append(copy.deepcopy(d.charts[0]))
  self.assertEqual(validate_document(d,[check()])[1][0]['error']['code'],'UNSUPPORTED_FORMAT')
 def test_independent_checks(self):
  status,rows=validate_document(document(),[check(current='missing'),check()])
  self.assertEqual(status,'ERROR');self.assertTrue(rows[1]['passed'])
 def test_input_validation(self):
  for changes in [dict(current_sign=True),dict(current_sign=0),dict(frequency_hz=0),dict(component='phase'),dict(frequency_sampling='nearest'),dict(min_current_a=0)]:
   with self.subTest(changes=changes),self.assertRaises(ValueError):validate_ac_request(check(**changes),True)
  for changes in [dict(max_points=1),dict(max_points=True),dict(components=['phase'])]:
   with self.subTest(changes=changes),self.assertRaises(ValueError):validate_graph_requests({'graphs':[graph(**changes)]})
 def test_sweep_directives_unchanged(self):
  for mode in ['DEC 100 10 1MEG','OCT 10 1 100','LIN 100 1 100']:
   text='* t\n.AC '+mode+'\n.END'
   self.assertEqual(generate_nsx({'spice':text},Path('.')),text+'\n')
 def test_graph_full_and_reduced(self):
  x=list(range(1,202));v=[complex(j,1) for j in x];d=document(x,v,[1+0j]*len(x))
  full=export_graphs(d,[graph()])[0];reduced=export_graphs(d,[graph(max_points=100)])[0]
  self.assertEqual(full['x'],x);self.assertEqual(full['real'],x)
  self.assertEqual(reduced['points_total'],201);self.assertEqual(reduced['points_returned'],100)
  self.assertEqual([reduced['x'][0],reduced['x'][-1]],[1,201])
  for c in ['real','imag','magnitude']:self.assertEqual(len(reduced[c]),100)
 def test_decimation_never_changes_checks(self):
  d=document([1,2,3],[1+0j,999+0j,1+0j],[1+0j]*3)
  self.assertEqual(validate_document(d,[check(frequency_hz=2,min=998,max=1000)])[0],'PASS')
  self.assertEqual(export_graphs(d,[graph(max_points=2)])[0]['real'],[1,1])
 def test_signal_graph(self):
  d=document();r=dict(name='v',type='signal',signal='v(dut)',components=['real','imag','magnitude'])
  self.assertEqual(export_graphs(d,[r])[0]['imag'],[0,0])
  wave=SDFWave('v(out)','V','NodeVoltage',2,[0.,1.],[2.,3.])
  for name,analysis,xunit in [('Transient Analysis','tran','s'),('DC Sweep','dc','V')]:
   waves=[wave]
   if analysis=='dc':waves=waves+[SDFWave('sweep','V','General',2,[0.,1.],[0.,1.])]
   doc=SDFDocument('1.0',[SDFChart(name,waves)])
   r=dict(name='out',type='signal',signal='v(out)')
   g=export_graphs(doc,[r])[0]
   self.assertEqual(g['analysis'],analysis);self.assertEqual(g['x_unit'],xunit)
   self.assertNotIn('imag',g)
 def test_complex_binary_layout(self):
  b=b'SDF: 1.0\nTotalPoints: 2\nChart: AC Analysis\nWave: v(x)\nWaveYUnitStr: V\nWavePoints: 2\nWaveIsXComplex: TRUE\nWaveIsYComplex: TRUE\nWaveExtendedDataStart\n'
  b+=struct.pack('<8d',10,0,1,2,20,0,3,-4)+b'\nWaveDataStart\n'+struct.pack('<8f',10,0,1,2,20,0,3,-4)+b'\n'
  w=parse_sdf_bytes(b).charts[0].waves[0]
  self.assertEqual(w.x,[10+0j,20+0j]);self.assertEqual(w.y,[1+2j,3-4j])
if __name__=='__main__':unittest.main()
