# SPDX-FileCopyrightText: 2026 nomatin
# SPDX-License-Identifier: GPL-3.0-only
import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from spice_sifter import parse_sdf,export_graphs


def main():
 results=[]
 with tempfile.TemporaryDirectory(dir=ROOT/'tests',prefix='spice_sifter_acceptance_ac_') as td:
  folder=Path(td)
  cases=[]
  for name in ['ac_resistor','ac_capacitor','ac_inductor','ac_rc','ac_graph_full','ac_graph_limited']:
   cases.append((name,json.loads((ROOT/'examples'/(name+'.json')).read_text()),'PASS'))
  failed=copy.deepcopy(cases[-1][1]);failed['checks'][0].update(min=2000,max=3000)
  cases.append(('fail_with_graphs',failed,'FAIL'))
  interp=json.loads((ROOT/'examples/ac_rc.json').read_text());interp['checks']=[dict(interp['checks'][3],frequency_hz=1011)]
  cases.append(('interpolation',interp,'PASS'))
  outside=copy.deepcopy(interp);outside['checks'][0]['frequency_hz']=1
  cases.append(('out_of_range',outside,'ERROR'))
  tran=json.loads((ROOT/'examples/divider.json').read_text());tran['output']={'graphs':[dict(name='out',type='signal',signal='v(out)',components=['real'],max_points=100)]}
  cases.append(('transient_graph',tran,'PASS'))
  dc=dict(name='dc_graph',spice='* DC divider\nV1 in 0 DC 0\nR1 in out 1k\nR2 out 0 1k\n.DC V1 0 5 1\n.PLOT DC V(out)\n.END',checks=[dict(analysis='dc',signal='v(out)',measurement='last',min=2.49,max=2.51)],output={'graphs':[dict(name='out',type='signal',analysis='dc',signal='v(out)')]})
  cases.append(('dc_graph',dc,'PASS'))
  for name,data,status in cases:
   path=folder/(name+'.json');path.write_text(json.dumps(data))
   run=subprocess.run([sys.executable,str(ROOT/'spice_sifter.py'),str(path),'--timeout','30','--keep-files',*sys.argv[1:]],capture_output=True,text=True,timeout=50)
   report=json.loads(run.stdout)
   ok=report['status']==status and run.returncode=={'PASS':0,'FAIL':1,'ERROR':2}[status]
   requested=data.get('output',{}).get('graphs')
   if requested and status!='ERROR':
    sdf=parse_sdf(Path(report['artifacts'])/'test.sdf')
    ok=ok and report.get('graphs')==export_graphs(sdf,requested)
    for request,g in zip(requested,report.get('graphs',[])):
     full_request=dict(request);full_request.pop('max_points',None)
     full=export_graphs(sdf,[full_request])[0]
     ok=ok and g['x'][0]==full['x'][0] and g['x'][-1]==full['x'][-1] and g['points_total']==len(full['x'])
     ok=ok and all(len(g[c])==len(g['x']) for c in request.get('components',['real']))
   elif not requested:ok=ok and 'graphs' not in report
   if name=='out_of_range':ok=ok and report['error']['code']=='AC_FREQUENCY_OUT_OF_RANGE'
   results.append(dict(case=name,passed=ok,exit=run.returncode,report=report))
   print(name,'OK' if ok else 'FAILED',flush=True)
 (ROOT/'tests/ac_acceptance_report.json').write_text(json.dumps(results,indent=2),encoding='utf-8')
 return 0 if all(r['passed'] for r in results) else 1
if __name__=='__main__':sys.exit(main())
