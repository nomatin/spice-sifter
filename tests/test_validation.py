# SPDX-FileCopyrightText: 2026 nomatin
# SPDX-License-Identifier: GPL-3.0-only
import unittest
from pathlib import Path
from spice_sifter import load_test, validate, generate_nsx
class ValidationTests(unittest.TestCase):
 def test_example(self):
  self.assertEqual(load_test('examples/divider.json')['name'],'voltage_divider')
 def test_outcomes(self):
  c=[dict(signal='V(out)',measurement='last',min=2.49,max=2.51)]
  for waves, status in [({'v(out)':[2.5]},'PASS'),({'v(out)':[3.1]},'FAIL'),({},'ERROR')]:
   self.assertEqual(validate(waves,c)[0],status)
 def test_independent(self):
  c=[dict(signal=s,measurement='last',min=0,max=3) for s in ['missing','v(out)']]
  status, rows=validate({'v(out)':[2.5]},c)
  self.assertEqual(status,'ERROR')
  self.assertTrue(rows[1]['passed'])
 def test_end(self):
  self.assertTrue(generate_nsx({'spice':'* t\n.TRAN 1u 1m'},Path('.')).endswith('.END\n'))
 def test_measurements_and_inclusive_bounds(self):
  checks=[dict(signal='v(x)',measurement=m,min=v,max=v) for m,v in [('first',3),('last',2),('min',1),('max',3)]]
  status,rows=validate({'V(X)':[3,1,2]},checks)
  self.assertEqual(status,'PASS')
  self.assertEqual([r['value'] for r in rows],[3,2,1,3])
 def test_invalid_waves(self):
  c=[dict(signal='v(x)',measurement='last',min=0,max=3)]
  for waves in [{'v(x)':[]},{'v(x)':[float('nan')]},{'v(x)':[complex(1,2)]},{'v(x)':[1],'V(X)':[2]}]:
   with self.subTest(waves=waves):self.assertEqual(validate(waves,c)[0],'ERROR')
 def test_unsupported_measurement(self):
  self.assertEqual(validate({'x':[1]},[dict(signal='x',measurement='average',min=0,max=2)])[0],'ERROR')
if __name__=='__main__': unittest.main()
