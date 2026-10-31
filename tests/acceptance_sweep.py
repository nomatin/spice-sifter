"""Opt-in Parameter Sweep acceptance against an open Altium 24.4."""
import copy,json,math,subprocess,sys,tempfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from spice_sifter import parse_sdf

def main():
 results=[]
 with tempfile.TemporaryDirectory(dir=ROOT/'tests',prefix='spice_sifter_acceptance_sweep_') as td:
  folder=Path(td);cases=[]
  for name,total in [('sweep_linear',6),('sweep_divider',6),('sweep_temperature',6),('sweep_ac',2),('sweep_transient',2),('sweep_op',2),('sweep_dc',2)]:
   cases.append((name,json.loads((ROOT/'examples'/(name+'.json')).read_text()),'PASS',total))
  broken=dict(name='one_simulation_error',spice='* Numeric model suffix sweep\nI1 0 out DC 1m\nD1 out 0 MOD{{M}}\n.MODEL MOD1 D(IS=1e-14 N=1)\n.MODEL MOD3 D(IS=1e-14 N=1)\n.TRAN 1u 10u\n.PLOT TRAN V(out)\n.END',sweep={'parameters':{'M':{'values':[1,2,3]}}},checks=[dict(signal='v(out)',measurement='last',min=.5,max=1)])
  cases.append(('one_simulation_error',broken,'ERROR',3))
  failed=copy.deepcopy(cases[1][1]);failed['checks'][0].update(min=20,max=30)
  cases.append(('one_fail_with_graphs',failed,'FAIL',6))
  too_many=copy.deepcopy(cases[1][1]);too_many['sweep']['parameters']['VIN']=dict(start=0,stop=1000,step=1)
  cases.append(('too_many',too_many,'ERROR',0))
  unresolved=copy.deepcopy(cases[1][1]);unresolved['spice']+='\n* {{UNKNOWN}}'
  cases.append(('unresolved',unresolved,'ERROR',0))
  for name,data,status,total in cases:
   path=folder/(name+'.json');path.write_text(json.dumps(data))
   p=subprocess.run([sys.executable,str(ROOT/'spice_sifter.py'),str(path),'--timeout','30','--keep-files',*sys.argv[1:]],capture_output=True,text=True,timeout=max(50,total*45))
   r=json.loads(p.stdout);ok=r['status']==status and p.returncode=={'PASS':0,'FAIL':1,'ERROR':2}[status]
   if total:
    runs=r.get('runs',[]);ok=ok and len(runs)==total and len({x['run_id'] for x in runs})==total
    for run in runs:
     nsx=(Path(run['artifacts'])/'test.nsx').read_text();ok=ok and '{{' not in nsx and 'parameters' in run
     for g in run.get('graphs',[]):
      ok=ok and g['run_id']==run['run_id'] and g['parameters']==run['parameters']
      if 'temperature_c' in run:ok=ok and g['temperature_c']==run['temperature_c']
     if name in ('sweep_linear','sweep_divider','sweep_temperature'):
      doc=parse_sdf(Path(run['artifacts'])/'test.sdf');w=next(w for c in doc.charts for w in c.waves if w.name=='v(out)')
      expected=run['parameters']['VIN']*1000/(run['parameters'].get('RLOAD',1000)+1000)
      ok=ok and math.isclose(w.y[-1],expected,abs_tol=.0001)
    if name in ('sweep_divider','one_fail_with_graphs'):
     ok=ok and [tuple(v['parameters'].values()) for v in runs]==[(2,1000),(2,2000),(5,1000),(5,2000),(10,1000),(10,2000)]
     ok=ok and [v['checks_count'] for v in runs]==[0,0,1,0,0,0] and ['graphs' in v for v in runs]==[False,False,True,True,False,False]
    if name=='sweep_temperature':
     ok=ok and [(v['temperature_c'],v['parameters']['VIN']) for v in runs]==[(-40,2),(-40,5),(25,2),(25,5),(85,2),(85,5)]
     ok=ok and ['graphs' in v for v in runs]==[False,True,False,False,False,True]
    if name=='one_simulation_error':ok=ok and [v['status'] for v in runs]==['PASS','ERROR','PASS'] and runs[1]['error']['code']=='SIM_NOT_RUN'
   else:
    ok=ok and r['error']['code']=='INPUT_INVALID' and 'runs' not in r and 'artifacts' not in r
    ok=ok and r['error'].get('sweep_code')==('SWEEP_TOO_MANY_RUNS' if name=='too_many' else 'SWEEP_PLACEHOLDER_MISSING')
   results.append(dict(case=name,passed=ok,exit=p.returncode,report=r));print(name,'OK' if ok else 'FAILED',flush=True)
 (ROOT/'tests/sweep_acceptance_report.json').write_text(json.dumps(results,indent=2),encoding='utf-8')
 return 0 if all(r['passed'] for r in results) else 1
if __name__=='__main__':sys.exit(main())
