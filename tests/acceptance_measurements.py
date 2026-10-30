"""Opt-in real MixedSim automatic-measurement acceptance."""
import argparse,copy,json,subprocess,sys,tempfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]

def cases():
 result={name:(json.loads((ROOT/'examples'/(name+'.json')).read_text()),'PASS') for name in ['measure_transient','measure_cutoff','measure_capacitor','measure_inductor','measure_resonance','measure_mosfet']}
 data=copy.deepcopy(result['measure_transient'][0]);data['measurements']=[dict(id='missing',type='at',signal='v(missing)',x=.002),dict(id='valid',type='mean',signal='v(out)',window=dict(start=.005,stop=.009)),dict(id='outside_limits',type='at',signal='v(out)',x=.002,limits=dict(min=10,max=20))]
 result['measurement_error_independence']=(data,'ERROR')
 return result

def verify(name,r,code,status):
 rows=r.get('measurements',[])
 ok=r['status']==status and code=={'PASS':0,'FAIL':1,'ERROR':2}[status] and bool(rows)
 if status=='PASS':ok=ok and all(m['status'] in ('PASS','MEASURED') for m in rows)
 for m in rows:
  if m.get('scope')=='sweep':ok=ok and bool(m.get('run_ids'))
  else:ok=ok and 'run_id' in m
 if name=='measure_transient':
  ok=ok and len({m['run_id'] for m in rows})==1 and r['graphs'][0]['points_returned']==10
 if name=='measure_mosfet':
  family=[m for m in rows if m['id']=='vgs_threshold'];rdson=[m for m in rows if m['id']=='rds_on']
  ok=ok and len(family)==3 and len(rdson)==3 and [m['temperature_c'] for m in family]==[-40,25,85]
  ok=ok and len(r['runs'])==24 and all(m['status']=='MEASURED' for m in family+rdson)
  ok=ok and len({run['run_id'] for run in r['runs']})==24
 if name=='measurement_error_independence':ok=ok and [m['status'] for m in rows]==['ERROR','MEASURED','FAIL'] and bool(r.get('graphs'))
 return ok

def main():
 parser=argparse.ArgumentParser();parser.add_argument('--case');args,extra=parser.parse_known_args()
 all_cases=cases();chosen={args.case:all_cases[args.case]} if args.case else all_cases
 output=ROOT/'tests/measurement_acceptance_report.json'
 previous=json.loads(output.read_text()) if output.exists() else []
 results={r['case']:r for r in previous}
 with tempfile.TemporaryDirectory(dir=ROOT/'tests',prefix='dbur_acceptance_measurement_') as td:
  for name,(data,status) in chosen.items():
   path=Path(td)/(name+'.json');path.write_text(json.dumps(data))
   p=subprocess.run([sys.executable,str(ROOT/'dbur_test.py'),str(path),'--timeout','30','--keep-files',*extra],capture_output=True,text=True,timeout=1200)
   r=json.loads(p.stdout);ok=verify(name,r,p.returncode,status)
   results[name]=dict(case=name,passed=ok,exit=p.returncode,report=r);print(name,'OK' if ok else 'FAILED',flush=True)
 output.write_text(json.dumps(list(results.values()),indent=2),encoding='utf-8')
 return 0 if all(results[name]['passed'] for name in chosen) else 1
if __name__=='__main__':sys.exit(main())
