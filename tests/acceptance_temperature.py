# SPDX-FileCopyrightText: 2026 nomatin
# SPDX-License-Identifier: GPL-3.0-only
"""Temperature acceptance against an already opened Altium Designer 24.4."""
import copy,json,re,subprocess,sys,tempfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]

def main():
 results=[]
 with tempfile.TemporaryDirectory(dir=ROOT/'tests',prefix='spice_sifter_acceptance_temp_') as td:
  folder=Path(td)
  cases=[]
  for name in ['temperature_single','temperature_series','temperature_ac','temperature_graphs']:
   cases.append((name,json.loads((ROOT/'examples'/(name+'.json')).read_text()),'PASS'))
  diode=copy.deepcopy(cases[1][1]);diode['output']={'graphs':[dict(name='v',type='signal',signal='v(out)')]}
  cases.append(('full_graphs',diode,'PASS'))
  divider=json.loads((ROOT/'examples/divider.json').read_text());divider['temperatures_c']=[-40,25,85]
  cases.append(('unchanged_divider',divider,'PASS'))
  failed=copy.deepcopy(cases[1][1]);failed['checks'][1]['signal']='v(unknown)'
  cases.append(('one_error',failed,'ERROR'))
  fail=copy.deepcopy(cases[1][1]);fail['checks'][1].update(min=1,max=2)
  cases.append(('one_fail',fail,'FAIL'))
  no_checks=copy.deepcopy(diode);no_checks['checks']=[]
  cases.append(('graphs_without_checks',no_checks,'PASS'))
  for name,data,status in cases:
   path=folder/(name+'.json');path.write_text(json.dumps(data))
   p=subprocess.run([sys.executable,str(ROOT/'spice_sifter.py'),str(path),'--timeout','30','--keep-files',*sys.argv[1:]],capture_output=True,text=True,timeout=150)
   report=json.loads(p.stdout);ok=report['status']==status and p.returncode=={'PASS':0,'FAIL':1,'ERROR':2}[status]
   runs=report.get('runs',[report]);temps=data.get('temperatures_c',[25])
   ok=ok and [r['temperature_c'] for r in runs]==temps and len({r['run_id'] for r in runs})==len(runs)
   for run in runs:
    nsx=(Path(run['artifacts'])/'test.nsx').read_text()
    matches=re.findall(r'^\.OPTIONS TEMP=([^\s]+)',nsx,re.M)
    ok=ok and len(matches)==1 and float(matches[0])==run['temperature_c']
    for g in run.get('graphs',[]):ok=ok and g['temperature_c']==run['temperature_c'] and len(g['x'])==g['points_returned']
   if name=='temperature_graphs':ok=ok and ['graphs' in r for r in runs]==[True,False,True]
   if name=='one_error':ok=ok and [r['status'] for r in runs]==['PASS','ERROR','PASS']
   if name=='one_fail':ok=ok and [r['status'] for r in runs]==['PASS','FAIL','PASS']
   if name=='graphs_without_checks':ok=ok and all(r['simulation_status']=='PASS' and r['checks_status']=='NOT_CONFIGURED' and r['checks_count']==0 for r in runs)
   if name=='full_graphs':ok=ok and all(r['graphs'][0]['points_total']==r['graphs'][0]['points_returned'] for r in runs)
   results.append(dict(case=name,passed=ok,exit=p.returncode,report=report));print(name,'OK' if ok else 'FAILED',flush=True)
 (ROOT/'tests/temperature_acceptance_report.json').write_text(json.dumps(results,indent=2),encoding='utf-8')
 return 0 if all(r['passed'] for r in results) else 1
if __name__=='__main__':sys.exit(main())
