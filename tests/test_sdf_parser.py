import unittest, struct
from spice_sifter import parse_sdf_bytes,TestError

def fixture(charts=1, complex_flag='FALSE',n=2):
 b=f'SDF: 1.0\nTotalPoints: {4*charts}\n'.encode()
 for c in range(charts):
  b+=f'Chart: Transient {c}\n'.encode()
  for name in ['v(out)','v(in)']:
   b+=f'Wave: {name}\nWaveYUnitStr: V\nWavePoints: {n}\nWaveIsXComplex: FALSE\nWaveIsYComplex: {complex_flag}\nWaveExtendedDataStart\n'.encode()
   b+=struct.pack('<4d',0,2.5,1,2.5)+b'\nWaveDataStart\n'+struct.pack('<4f',0,2.5,1,2.5)+b'\n'
 return b
class ParserTests(unittest.TestCase):
 def test_values(self):
  d=parse_sdf_bytes(fixture())
  self.assertEqual(d.charts[0].waves[0].y,[2.5,2.5])
 def test_multiple(self):
  d=parse_sdf_bytes(fixture(2))
  self.assertEqual([len(c.waves) for c in d.charts],[2,2])
 def test_reject(self):
  for b in [b'bad\n',fixture()[:-2],fixture(n=3),fixture(complex_flag='TRUE')]:
   with self.subTest(data=b[:30]):
    with self.assertRaises(TestError):parse_sdf_bytes(b)
 def test_total_point_count(self):
  with self.assertRaises(TestError):parse_sdf_bytes(fixture().replace(b'TotalPoints: 4',b'TotalPoints: 5'))
 def test_one_signal(self):
  b=fixture(); second=b.index(b'Wave: v(in)')
  d=parse_sdf_bytes(b[:second].replace(b'TotalPoints: 4',b'TotalPoints: 2'))
  self.assertEqual(len(d.charts[0].waves),1)
if __name__=='__main__':unittest.main()
