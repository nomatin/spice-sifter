"""Opt-in acceptance checks against an already running Altium 24.4."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT=Path(__file__).resolve().parents[1]

def main():
 results=[]
 with tempfile.TemporaryDirectory(prefix='dbur_acceptance_',dir=ROOT/'tests') as td:
  folder=Path(td)
  divider=json.loads((ROOT/'examples/divider.json').read_text())
  cases=[]
  for name,status,code in [('divider','PASS',None),('wrong_range','FAIL',None),('missing_signal','ERROR','SIGNAL_NOT_FOUND'),('invalid_json','ERROR','INPUT_INVALID'),('repeat','PASS',None),('invalid_model','ERROR','SIM_NOT_RUN')]:
   data=json.loads(json.dumps(divider))
   if name=='wrong_range':data['checks'][0].update(min=3,max=3.2)
   if name=='missing_signal':data['checks'][0]['signal']='v(unknown)'
   if name=='invalid_json':del data['spice']
   if name=='invalid_model':data['spice']=data['spice'].replace('R1 in out 10k','D1 in out NO_SUCH_MODEL')
   cases.append((name,data,status,code))
  diode=json.loads((ROOT/'examples/diode.json').read_text())
  model=folder/'модель диода.lib';model.write_bytes((ROOT/'examples/models/diode.lib').read_bytes())
  diode['models']=[model.name]
  cases.append(('кириллица и пробелы',diode,'PASS',None))
  for name,data,expected,code in cases:
   path=folder/(name+'.json');path.write_text(json.dumps(data),encoding='utf-8')
   run=subprocess.run([sys.executable,str(ROOT/'dbur_test.py'),str(path),'--timeout','30','--keep-files',*sys.argv[1:]],capture_output=True,text=True,timeout=50)
   report=json.loads(run.stdout)
   ok=(report['status']==expected and run.returncode=={'PASS':0,'FAIL':1,'ERROR':2}[expected] and (code is None or report['error']['code']==code))
   results.append(dict(case=name,passed=ok,exit=run.returncode,report=report))
   print(name, 'OK' if ok else 'FAILED',flush=True)
  artifacts=[r['report']['artifacts'] for r in results if 'artifacts' in r['report']]
  assert len(artifacts)==len(set(artifacts)), 'Reused run directory'
 out=ROOT/'tests/acceptance_report.json'
 out.write_text(json.dumps(results,indent=2,ensure_ascii=False),encoding='utf-8')
 return 0 if all(r['passed'] for r in results) else 1
if __name__=='__main__':sys.exit(main())
