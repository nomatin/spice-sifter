"""DBUR Test: input preparation and independent result validation.
Parameter/temperature series, AC impedance, transient/DC/OP checks and numeric graph export.
Validated against Altium Designer 24.4.1.13 on Windows.
"""
import argparse
import json
import math
import os
from pathlib import Path
import re
import sys

class TestError(Exception):
    def __init__(self, code, message):
        self.code, self.message = code, message
        super().__init__(message)

def load_test(path,max_runs=100):
    try:
        data = json.loads(Path(path).read_text(encoding='utf-8-sig'), parse_constant=lambda x: (_ for _ in ()).throw(ValueError('Nonstandard JSON number: '+x)))
        if not isinstance(data, dict):
            raise ValueError('Expected an object')
        for key in ('name', 'spice'):
            if not isinstance(data.get(key), str) or not data[key].strip():
                raise ValueError(f'{key} must be a nonempty string')
        plan=temperature_plan(data)
        if not isinstance(data.get('checks'), list) or (not data['checks'] and plan is None and 'sweep' not in data):
            raise ValueError('checks must be a nonempty array')
        for c in data['checks']:
            if not isinstance(c, dict):
                raise ValueError('Each check must be an object')
            if c.get('analysis') == 'ac':
                validate_ac_request(c, check=True)
            else:
                if not isinstance(c.get('signal'), str) or not c['signal'].strip():
                    raise ValueError('Invalid signal')
                if c.get('analysis') not in (None, 'tran', 'dc', 'op'):
                    raise ValueError('Unsupported analysis')
                if c.get('measurement') not in ('first', 'last', 'min', 'max'):
                    raise ValueError('Unsupported measurement')
            for k in ('min', 'max'):
                v = c.get(k)
                if isinstance(v, bool) or not isinstance(v, (float, int)) or not math.isfinite(v):
                    raise ValueError('Bounds must be finite numbers')
            if c['min'] > c['max']:
                raise ValueError('Reversed bounds')
        if not isinstance(data.get('models', []), list):
            raise ValueError('models must be an array')
        for model in data.get('models', []):
            if not isinstance(model, str) or not model or any(x in model for x in '\r\n"'):
                raise ValueError('Invalid model path')
            if not (Path(path).resolve().parent / model).is_file():
                raise ValueError(f'Model not found: {model}')
        validate_graph_requests(data.get('output', {}))
        sweep_plan(data,max_runs,Path(path).resolve().parent)
        generate_nsx(data, Path(path).resolve().parent)
        return data
    except (ValueError, OSError, TypeError) as e:
        raise TestError('INPUT_INVALID', str(e)) from e

def model_path(path):
    value=str(path.resolve())
    if any(c in value for c in '\r\n\"'):
        raise TestError('INPUT_INVALID','Invalid character in model path')
    if os.name == 'nt' and not value.isascii():
        import ctypes
        buffer=ctypes.create_unicode_buffer(32768)
        count=ctypes.windll.kernel32.GetShortPathNameW(value,buffer,len(buffer))
        if count and count < len(buffer) and buffer.value.isascii():
            value=buffer.value
        else:
            raise TestError('INPUT_INVALID','MixedSim requires an ASCII model path or available Windows short name: '+value)
    return value


def generate_nsx(data, base):
    lines = data['spice'].splitlines()
    if not re.search(r'^\s*\.(TRAN|AC|DC|OP)\b', '\n'.join(lines[1:]), re.I | re.M):
        raise ValueError('Requires .TRAN, .AC, .DC or .OP after the title line')
    ends = [i for i, line in enumerate(lines) if re.fullmatch(r'\s*\.END\s*', line, re.I)]
    if len(ends) > 1 or (ends and any(s.strip() and not s.lstrip().startswith('*') for s in lines[ends[0]+1:])):
        raise ValueError('Invalid .END position')
    includes = [f'.include "{model_path(base / m)}"' for m in data.get('models', [])]
    temperature_plan(data)
    # AD24.4 standalone NSX ignores .TEMP; OPTIONS TEMP was experimentally verified.
    lines=[re.sub(r'^(\s*)\.TEMP\s+([^;$]+)',lambda m:m[1]+'.OPTIONS TEMP='+m[2].strip()+' ',line,flags=re.I) for line in lines]
    lines[1:1] = includes
    if not ends:
        lines.append('.END')
    return '\n'.join(lines) + '\n'

def validate(waves, checks):
    results = []
    for c in checks:
        r = dict(c, value=None, passed=False)
        try:
            matches = [v for k, v in waves.items() if k.casefold() == c['signal'].casefold()]
            if len(matches) != 1:
                raise TestError('SIGNAL_NOT_FOUND', 'Signal absent or ambiguous: ' + c['signal'])
            values = matches[0]
            if not values or any(isinstance(v, complex) or not math.isfinite(v) for v in values):
                raise TestError('UNSUPPORTED_FORMAT', 'Empty, complex or nonfinite signal')
            op = c['measurement']
            if op not in ('first', 'last', 'min', 'max'):
                raise TestError('INPUT_INVALID', 'Unsupported measurement')
            value = {'first': lambda: values[0], 'last': lambda: values[-1], 'min': lambda: min(values), 'max': lambda: max(values)}[op]()
            r.update(value=value, passed=c['min'] <= value <= c['max'])
        except TestError as e:
            r['error'] = dict(code=e.code, message=e.message)
        results.append(r)
    status = 'ERROR' if any('error' in r for r in results) else ('PASS' if all(r['passed'] for r in results) else 'FAIL')
    return status, results


COMPONENTS = ('real', 'imag', 'magnitude')


def finite_number(v):
    return not isinstance(v, bool) and isinstance(v, (int, float)) and math.isfinite(v)


def validate_ac_request(r, check=False):
    if check and r.get('quantity') != 'impedance':
        raise ValueError('AC quantity must be impedance')
    for key in ('voltage', 'current'):
        if not isinstance(r.get(key), str) or not r[key].strip():
            raise ValueError(key+' must name a signal')
    if isinstance(r.get('current_sign'), bool) or r.get('current_sign') not in (1,-1):
        raise ValueError('current_sign must be 1 or -1')
    if not finite_number(r.get('min_current_a',1e-15)) or r.get('min_current_a',1e-15)<=0:
        raise ValueError('min_current_a must be positive and finite')
    if check:
        if not finite_number(r.get('frequency_hz')) or r['frequency_hz']<=0:
            raise ValueError('frequency_hz must be positive and finite')
        if r.get('component') not in COMPONENTS or r.get('frequency_sampling','interpolate') not in ('exact','interpolate'):
            raise ValueError('Invalid AC component or sampling mode')


def validate_graph_requests(output):
    if not isinstance(output,dict) or not isinstance(output.get('graphs',[]),list):
        raise ValueError('output.graphs must be an array')
    names=set()
    for r in output.get('graphs',[]):
        if not isinstance(r,dict) or not isinstance(r.get('name'),str) or not r['name'].strip() or r['name'] in names:
            raise ValueError('Each graph needs a unique nonempty name')
        names.add(r['name'])
        if r.get('type')=='impedance':
            validate_ac_request(r)
            if r.get('analysis','ac')!='ac':raise ValueError('Impedance requires AC')
        elif r.get('type')=='signal':
            if not isinstance(r.get('signal'),str) or not r['signal'].strip():raise ValueError('Graph signal required')
            if r.get('analysis') not in (None,'ac','tran','dc','op'):raise ValueError('Invalid graph analysis')
        else:raise ValueError('Unknown graph type')
        cs=r.get('components',['real'])
        if not isinstance(cs,list) or not cs or any(c not in COMPONENTS for c in cs) or len(cs)!=len(set(cs)):
            raise ValueError('Invalid graph components')
        m=r.get('max_points')
        if 'max_points' in r and (isinstance(m,bool) or not isinstance(m,int) or m<2):
            raise ValueError('max_points must be an integer >= 2 to preserve endpoints')


def chart_analysis(chart):
    return {'transient analysis':'tran','ac analysis':'ac','dc sweep':'dc','operating point':'op'}.get(chart.name.casefold())


def select_chart(doc, analysis, signals):
    candidates=[c for c in doc.charts if analysis is None or chart_analysis(c)==analysis]
    if not candidates:
        raise TestError('AC_CHART_NOT_FOUND' if analysis=='ac' else 'GRAPH_EXPORT_ERROR','Requested analysis not found')
    matching=[c for c in candidates if all(any(w.name.casefold()==s.casefold() for w in c.waves) for s in signals)]
    if len(matching)>1:
        raise TestError('UNSUPPORTED_FORMAT','Ambiguous charts: cannot select a run safely')
    if not matching:
        raise TestError('AC_WAVE_NOT_FOUND' if analysis=='ac' else 'SIGNAL_NOT_FOUND','Signals not found in one chart: '+', '.join(signals))
    return matching[0]


def find_wave(chart,name,ac=False):
    waves=[w for w in chart.waves if w.name.casefold()==name.casefold()]
    if len(waves)!=1:
        raise TestError('AC_WAVE_NOT_FOUND' if ac else 'SIGNAL_NOT_FOUND','Signal missing or ambiguous: '+name)
    return waves[0]


def finite_values(values):
    for v in values:
        if not math.isfinite(v.real) or not math.isfinite(v.imag):
            raise TestError('NON_FINITE_RESULT','Nonfinite waveform data')


def frequency_axis(w):
    finite_values(w.x);finite_values(w.y)
    if not w.is_complex or not all(isinstance(v,complex) for v in w.y):
        raise TestError('AC_COMPLEX_NOT_AVAILABLE','Complex data unavailable: '+w.name)
    x=[]
    for v in w.x:
        if isinstance(v,complex) and v.imag!=0:
            raise TestError('UNSUPPORTED_FORMAT','Complex-valued frequency axis')
        x.append(v.real)
    if len(x)!=len(w.y) or not x or any(v<=0 for v in x) or any(b<=a for a,b in zip(x,x[1:])):
        raise TestError('AC_FREQUENCY_GRID_MISMATCH','Frequency axis must be positive and strictly increasing')
    return x


def impedance_waves(doc,r):
    chart=select_chart(doc,'ac',[r['voltage'],r['current']])
    v=find_wave(chart,r['voltage'],True);i=find_wave(chart,r['current'],True)
    if v.unit.casefold()!='v' or i.unit.casefold()!='a':
        raise TestError('AC_WAVE_NOT_FOUND','Impedance requires voltage in V and current in A')
    x=frequency_axis(v);ix=frequency_axis(i)
    if len(x)!=len(ix) or any(a!=b for a,b in zip(x,ix)):
        raise TestError('AC_FREQUENCY_GRID_MISMATCH','Voltage/current frequency grids differ')
    return x,v.y,i.y


def impedance(v,i,r):
    finite_values([v,i])
    if math.hypot(i.real,i.imag)<r.get('min_current_a',1e-15):
        raise TestError('IMPEDANCE_DIVISION_BY_ZERO','Current is zero or below min_current_a')
    z=v/(r['current_sign']*i)
    finite_values([z])
    return z


def sampled(x,values,f,mode):
    import bisect
    idx=bisect.bisect_left(x,f)
    for j in (idx,idx-1):
        if 0<=j<len(x) and math.isclose(x[j],f,rel_tol=1e-10,abs_tol=0):return values[j]
    if f<x[0] or f>x[-1]:
        raise TestError('AC_FREQUENCY_OUT_OF_RANGE','Requested frequency is outside sweep')
    if mode=='exact':
        raise TestError('AC_FREQUENCY_OUT_OF_RANGE','Exact frequency not present in sweep')
    t=(f-x[idx-1])/(x[idx]-x[idx-1])
    return values[idx-1]*(1-t)+values[idx]*t


def component_value(z,component):
    v={'real':lambda:z.real,'imag':lambda:z.imag,'magnitude':lambda:math.hypot(z.real,z.imag)}[component]()
    finite_values([v])
    return v


def validate_document(doc,checks):
    results=[]
    for c in checks:
        if c.get('analysis')!='ac':
            try:
                chart=select_chart(doc,c.get('analysis','tran'),[c['signal']])
                matches=[w for w in chart.waves if w.name.casefold()==c['signal'].casefold()]
                if len(matches)!=1:raise TestError('SIGNAL_NOT_FOUND','Signal absent or ambiguous: '+c['signal'])
                _,out=validate({matches[0].name:matches[0].y},[c]);results.extend(out)
            except TestError as e:
                results.append(dict(c,value=None,passed=False,error=dict(code=e.code,message=e.message)))
            continue
        row=dict(c,value=None,unit='Ohm',passed=False)
        try:
            x,v,i=impedance_waves(doc,c)
            f=c['frequency_hz'];mode=c.get('frequency_sampling','interpolate')
            z=impedance(sampled(x,v,f,mode),sampled(x,i,f,mode),c)
            value=component_value(z,c['component'])
            row.update(value=value,passed=c['min']<=value<=c['max'])
        except TestError as e:row['error']=dict(code=e.code,message=e.message)
        results.append(row)
    status='ERROR' if any('error' in r for r in results) else ('PASS' if all(r['passed'] for r in results) else 'FAIL')
    return status,results


def export_graphs(doc,requests):
    graphs=[]
    for r in requests:
        if r['type']=='impedance':
            x,v,i=impedance_waves(doc,r)
            values=[impedance(a,b,r) for a,b in zip(v,i)]
            analysis='ac';unit='Ohm'
        else:
            chart=select_chart(doc,r.get('analysis'),[r['signal']])
            wave=find_wave(chart,r['signal'],chart_analysis(chart)=='ac')
            analysis=chart_analysis(chart)
            if analysis is None:raise TestError('GRAPH_EXPORT_ERROR','Unknown analysis units')
            if analysis=='ac':x=frequency_axis(wave)
            else:
                x=wave.x
                finite_values(x);finite_values(wave.y)
                if any(isinstance(v,complex) for v in x+wave.y):raise TestError('GRAPH_EXPORT_ERROR','Expected real waveform')
            values=wave.y;unit=wave.unit
            if analysis!='ac' and any(c!='real' for c in r.get('components',['real'])):
                raise TestError('GRAPH_EXPORT_ERROR','Real signals support only the real component')
        n=len(x)
        if n!=len(values) or n==0:raise TestError('GRAPH_EXPORT_ERROR','Invalid waveform length')
        limit=min(n,r.get('max_points',n))
        indices=list(range(n)) if limit==n else [j*(n-1)//(limit-1) for j in range(limit)]
        xunit='Hz' if analysis=='ac' else ('1' if analysis=='op' else 's')
        if analysis=='dc':
            units={w.unit for w in chart.waves if w.x==w.y and w.x==x and w.unit}
            if len(units)!=1:raise TestError('GRAPH_EXPORT_ERROR','Cannot determine DC sweep units unambiguously')
            xunit=next(iter(units))
        graph=dict(name=r['name'],analysis=analysis,x_unit=xunit,y_unit=unit,points_total=n,points_returned=len(indices),x=[x[j] for j in indices])
        for component in r.get('components',['real']):
            graph[component]=[component_value(values[j],component) for j in indices]
        graphs.append(graph)
    return graphs


from dataclasses import dataclass, field
import struct

@dataclass
class SDFWave:
    name: str
    unit: str
    signal_type: str
    point_count: int
    x: list
    y: list
    is_complex: bool = False

@dataclass
class SDFChart:
    name: str
    waves: list = field(default_factory=list)
    metadata: dict = field(default_factory=dict)

@dataclass
class SDFDocument:
    version: str
    charts: list = field(default_factory=list)
    metadata: dict = field(default_factory=dict)

def parse_sdf(path):
    try:
        return parse_sdf_bytes(Path(path).read_bytes())
    except OSError as e:
        raise TestError('SDF_PARSE_ERROR', str(e)) from e

def parse_sdf_bytes(data):
    # Observed SDF 1.0: interleaved X,Y doubles followed by X,Y singles.
    pos = 0
    def line():
        nonlocal pos
        end = data.find(b'\n', pos)
        if end < 0:
            raise TestError('SDF_PARSE_ERROR', 'Truncated text record')
        raw = data[pos:end].rstrip(b'\r')
        pos = end + 1
        try:
            return raw.decode('utf-8')
        except UnicodeDecodeError as e:
            raise TestError('UNSUPPORTED_FORMAT', 'Unknown text encoding') from e
    def consume(raw):
        nonlocal pos
        if data[pos:pos+len(raw)] != raw:
            raise TestError('SDF_PARSE_ERROR', 'Unexpected binary boundary at '+str(pos))
        pos += len(raw)
    if line() != 'SDF: 1.0':
        raise TestError('UNSUPPORTED_FORMAT', 'Only observed SDF 1.0 layout is supported')
    doc = SDFDocument('1.0')
    chart = None
    wave_name = None
    wm = {}
    setup = False
    while pos < len(data):
        text = line()
        if text == '#ChartSetupsBegin':
            setup = True
            continue
        if text == '#ChartSetupsEnd':
            setup = False
            continue
        if setup or not text:
            continue
        if text == 'WaveExtendedDataStart':
            if chart is None or wave_name is None:
                raise TestError('SDF_PARSE_ERROR', 'Wave without chart or name')
            try:
                n = int(wm['WavePoints'])
                if n <= 0 or n > len(data)//24:
                    raise ValueError('Invalid point count')
                flags=[wm.get('WaveIsXComplex'),wm.get('WaveIsYComplex')]
                if any(f not in ('TRUE','FALSE') for f in flags):
                    raise TestError('UNSUPPORTED_FORMAT','Invalid complex flags')
                xc,yc=[f=='TRUE' for f in flags]
                width=2+int(xc)+int(yc)
                size = 8*width*n
                if pos + size > len(data):
                    raise ValueError('Truncated double block')
                raw=list(struct.iter_unpack('<'+'d'*width,data[pos:pos+size]))
                values=[(complex(v[0],v[1]) if xc else v[0],complex(v[1+int(xc)],v[2+int(xc)]) if yc else v[1+int(xc)]) for v in raw]
                pos += size
                consume(b'\nWaveDataStart\n')
                size = 4*width*n
                if pos + size > len(data):
                    raise ValueError('Truncated single block')
                pos += size
                consume(b'\n')
                chart.waves.append(SDFWave(wave_name, wm.get('WaveYUnitStr',''),wm.get('WaveVariableType',''),n,[v[0] for v in values],[v[1] for v in values],yc))
                wave_name = None
                wm = {}
            except (KeyError, ValueError, struct.error) as e:
                raise TestError('SDF_PARSE_ERROR', str(e)) from e
            continue
        if ':' not in text:
            raise TestError('UNSUPPORTED_FORMAT', 'Unknown SDF record: '+text[:80])
        key, value = text.split(':',1)
        value = value.strip()
        if key == 'Chart':
            if wave_name is not None:
                raise TestError('SDF_PARSE_ERROR', 'Incomplete wave')
            chart = SDFChart(value)
            doc.charts.append(chart)
        elif key == 'Wave':
            if wave_name is not None:
                raise TestError('SDF_PARSE_ERROR', 'Incomplete wave')
            wave_name, wm = value, {}
        elif wave_name is not None:
            wm[key] = value
        elif chart is not None:
            chart.metadata[key] = value
        else:
            doc.metadata[key] = value
    if setup or wave_name is not None or not any(c.waves for c in doc.charts):
        raise TestError('SDF_PARSE_ERROR', 'Incomplete or empty SDF')
    if 'TotalPoints' in doc.metadata:
        try:
            total=int(doc.metadata['TotalPoints'])
        except ValueError as e:
            raise TestError('SDF_PARSE_ERROR','Invalid TotalPoints') from e
        if total != sum(w.point_count for c in doc.charts for w in c.waves):
            raise TestError('SDF_PARSE_ERROR','TotalPoints does not match wave point counts')
    return doc


import subprocess
import tempfile
import time
import uuid
import shutil


def pas_string(value):
    # ASCII script source also supports Cyrillic paths through character literals.
    return "'" + ''.join(("''" if c == "'" else c) if ord(c)<128 else "'#"+str(ord(c))+"'" for c in str(value)) + "'"


def require_running_altium(exe):
    """Require the selected installation, excluding transient -R launchers."""
    command = ("[Console]::OutputEncoding = [System.Text.Encoding]::UTF8; Get-CimInstance Win32_Process -Filter \"name='X2.EXE'\" "
               "-ErrorAction Stop | Select-Object ExecutablePath,CommandLine,@{Name='Responding';Expression={(Get-Process -Id $_.ProcessId -ErrorAction Stop).Responding}} | ConvertTo-Json -Compress")
    try:
        result = subprocess.run(
            ['powershell', '-NoProfile', '-Command', command],
            capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=10)
        if result.returncode:
            raise ValueError('Windows process query failed')
        processes = json.loads(result.stdout) if result.stdout.strip() else []
        if isinstance(processes, dict):
            processes = [processes]
        expected = os.path.normcase(os.path.abspath(exe))
        for process in processes:
            path = process.get('ExecutablePath')
            command_line = process.get('CommandLine') or ''
            if (path and os.path.normcase(os.path.abspath(path)) == expected
                    and not re.search(r'(?:^|[\s"])\-R', command_line, re.I)):
                if process.get('Responding') is False:
                    raise TestError('BUSY','Selected Altium process is not responding')
                return
    except (OSError, ValueError, subprocess.TimeoutExpired) as e:
        raise TestError('ALTIUM_STATE_UNKNOWN', 'Cannot verify running Altium: '+str(e)) from e
    raise TestError('ALTIUM_NOT_RUNNING', 'Start the selected Altium Designer before running the test')


def read_status(path):
    try:
        text=path.read_text(encoding='utf-8-sig')
    except FileNotFoundError:
        return {}
    return dict(line.split('=',1) for line in text.splitlines() if '=' in line)


def run_altium(exe, data, base, timeout, keep, report, verbose=False):
    root = Path(tempfile.gettempdir()) / 'DBUR-Test'
    root.mkdir(exist_ok=True)
    guard=root/'pending-run.txt'
    import msvcrt
    lock = (root / 'altium.lock').open('a+b')
    lock.seek(0)
    if not lock.read(1):
        lock.write(b'0'); lock.flush()
    lock.seek(0)
    try:
        msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
    except OSError:
        lock.close()
        raise TestError('BUSY','Another DBUR run holds the Altium lock')
    if guard.exists():
        pending=Path(guard.read_text(encoding='utf-8'))
        previous=read_status(pending/'status.txt')
        if previous.get('STATUS') not in ('OK','ERROR'):
            msvcrt.locking(lock.fileno(),msvcrt.LK_UNLCK,1)
            lock.close()
            raise TestError('BUSY','Previous timed-out run has not completed: '+str(pending))
        guard.unlink()
    work = root / uuid.uuid4().hex
    work.mkdir()
    if 'temperature_c' in report or 'parameters' in report:report['run_id']=work.name
    success = False
    timed_out = False
    try:
        nsx = work/'test.nsx'
        nsx.write_text(generate_nsx(data,base),encoding='utf-8')
        status = work/'status.txt'
        script = work/('run_'+uuid.uuid4().hex+'.pas')
        expected = work/'test.sdf'
        script.write_text("""procedure RunTests;
var S, D;
begin
 S := TStringList.Create;
 try
  S.Add('STARTED');
  S.SaveToFile(%s);
  ResetParameters;
  AddStringParameter('ObjectKind', 'Document');
  AddStringParameter('FileName', %s);
  RunProcess('WorkspaceManager:OpenObject');
  D := Client.GetDocumentByPath(%s);
  if D = Nil then begin
   S.Add('STATUS=ERROR'); S.Add('CODE=SIM_NOT_RUN');
  end else begin
   Client.ShowDocument(D);
   RunProcess('ADVSIM:RunSimulator');
   D := Client.CurrentView.OwnerDocument;
   if LowerCase(D.FileName) <> LowerCase(%s) then begin
    S.Add('STATUS=ERROR'); S.Add('CODE=SIM_NOT_RUN');
   end else begin
    if D.DoFileSave('') then begin
     S.Add('STATUS=OK'); S.Add('SDF_PATH=' + D.FileName);
    end else begin
     S.Add('STATUS=SAVE_PENDING'); S.Add('CODE=SDF_NOT_SAVED');
    end;
   end;
  end;
 except
  S.Add('STATUS=ERROR'); S.Add('CODE=SIM_NOT_RUN');
 end;
 S.SaveToFile(%s);
 RenameFile(%s, %s);
 S.Free;
end;
""" % tuple(map(pas_string,[work/'started.txt',nsx,nsx,expected,work/'status.tmp',work/'status.tmp',status])),encoding='ascii')
        command = '"' + str(exe) + '" -RScriptingSystem:RunScriptFile(FileName="' + str(script) + '"|ProcName="RunTests")'
        (work/'run.log').write_text(command,encoding='utf-8')
        if verbose:
            print('Run directory: '+str(work),file=sys.stderr)
        subprocess.Popen(command,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        deadline=time.monotonic()+timeout
        collection_sent=False
        while True:
            if status.exists():
                fields=read_status(status)
                if fields.get('STATUS') == 'SAVE_PENDING':
                    if not collection_sent:
                        collection_sent=True
                        time.sleep(0.5)
                        collect=work/'collect.pas'
                        collect.write_text("""procedure RunTests;
var D, S;
begin
 S := TStringList.Create;
 try
  D := Client.GetDocumentByPath(%s);
  if D <> Nil then D.DoFileSave('');
  if FileExists(%s) then begin
   S.Add('STATUS=OK'); S.Add('SDF_PATH=' + D.FileName);
  end else begin
   S.Add('STATUS=ERROR'); S.Add('CODE=SDF_NOT_SAVED');
  end;
 except
  S.Add('STATUS=ERROR'); S.Add('CODE=SDF_NOT_SAVED');
 end;
 S.SaveToFile(%s);
 DeleteFile(%s); RenameFile(%s,%s);
 S.Free;
end;
""" % tuple(map(pas_string,[expected,expected,work/'collect.tmp',status,work/'collect.tmp',status])),encoding='ascii')
                        subprocess.Popen('"'+str(exe)+'" -RScriptingSystem:RunScriptFile(FileName="'+str(collect)+'"|ProcName="RunTests")',stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
                elif fields.get('STATUS') == 'ERROR':
                    raise TestError(fields.get('CODE','SIM_NOT_RUN'),'MixedSim script failed; inspect artifacts')
                if fields.get('STATUS') == 'OK':
                    result=Path(fields.get('SDF_PATH',''))
                    if result.resolve() != expected.resolve() or not result.is_file():
                        raise TestError('SDF_NOT_SAVED','Expected current-run SDF was not saved')
                    doc=parse_sdf(result)
                    if 'temperature_c' in report or 'parameters' in report:report['simulation_status']='PASS'
                    outcome,rows=validate_document(doc,data['checks'])
                    report['checks']=rows
                    if data.get('output',{}).get('graphs'):
                        report['graphs']=export_graphs(doc,data['output']['graphs'])
                    success=True
                    return outcome,rows
            if time.monotonic()>=deadline:
                timed_out=True
                code='TIMEOUT' if (work/'started.txt').exists() else 'SCRIPT_NOT_EXECUTED'
                raise TestError(code,'Altium did not write completion status before timeout')
            time.sleep(0.1)
    finally:
        if keep or not success:
            report['artifacts']=str(work)
            report['diagnostics']={'script_started':(work/'started.txt').exists(),'status':read_status(work/'status.txt'),'files':[p.name for p in work.iterdir() if p.is_file()]}
        else:
            shutil.rmtree(work,ignore_errors=True)
        if timed_out:
            # A timed-out script may still run. Preserve a guard for subsequent calls.
            (root/'pending-run.txt').write_text(str(work),encoding='utf-8')
        msvcrt.locking(lock.fileno(),msvcrt.LK_UNLCK,1)
        lock.close()

class TemperatureInputError(TestError):
    def __init__(self, reason, message):
        super().__init__('INPUT_INVALID',message)
        self.temperature_code=reason


def temperature_value(value):
    if not finite_number(value) or value<=-273.15:
        raise TemperatureInputError('TEMP_INVALID','Temperature must be finite and above -273.15 C')
    return value


def temperature_list(values):
    if not isinstance(values,list) or not values:
        raise TemperatureInputError('TEMP_INVALID','temperatures_c must be a nonempty array')
    for v in values:temperature_value(v)
    if len(set(values))!=len(values):
        raise TemperatureInputError('TEMP_INVALID','Duplicate temperatures')
    return values


def temperature_plan(data):
    records=[]
    for raw in data['spice'].splitlines()[1:]:
        line=re.split(r'[;$]',raw,maxsplit=1)[0].strip()
        if not line or line.startswith('*'):continue
        if line.startswith('+') and records:
            records[-1]+=' '+line[1:]
        else:records.append(line)
    temps=[];options=False;depth=0
    for line in records:
        token=line.split()[0].casefold()
        if token in ('.subckt','.control','.if','.alter') or (token=='.lib' and len(line.split())==2):depth+=1
        if token in ('.ends','.endc','.endif','.endl'):depth=max(0,depth-1)
        if token in ('.options','.option') and re.search(r'\bTEMP(?:\s*=|\s+|$)',line,re.I):options=True
        if token=='.temp':
            if depth:raise TemperatureInputError('TEMP_CONFLICT','.TEMP must be a top-level directive')
            parts=line.split()
            if len(parts)!=2 or not re.fullmatch(r'[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?',parts[1]):
                raise TemperatureInputError('TEMP_INVALID','.TEMP requires exactly one numeric value')
            temps.append(temperature_value(float(parts[1])))
    if len(temps)>1:raise TemperatureInputError('TEMP_CONFLICT','Multiple active .TEMP directives')
    if temps and options:raise TemperatureInputError('TEMP_CONFLICT','.TEMP conflicts with .OPTIONS TEMP')
    if 'temperatures_c' in data:
        values=temperature_list(data['temperatures_c'])
        if temps or options:raise TemperatureInputError('TEMP_CONFLICT','JSON temperatures conflict with SPICE temperature directives')
    else:values=temps or None
    for c in data.get('checks',[]):
        if isinstance(c,dict) and 'temperature_c' in c:
            t=temperature_value(c['temperature_c'])
            if values is None or t not in values:
                raise TemperatureInputError('TEMP_CHECK_NOT_FOUND','Check refers to a temperature outside this test')
    output=data.get('output',{})
    if isinstance(output,dict) and isinstance(output.get('graphs',[]),list):
        for g in output.get('graphs',[]):
            if isinstance(g,dict) and 'temperatures_c' in g:
                requested=temperature_list(g['temperatures_c'])
                if values is None or any(t not in values for t in requested):
                    raise TemperatureInputError('TEMP_GRAPH_NOT_FOUND','Graph filter refers to a temperature outside this test')
    return values


def error_payload(error):
    payload=dict(code=error.code,message=error.message)
    if hasattr(error,'temperature_code'):payload['temperature_code']=error.temperature_code
    if hasattr(error,'sweep_code'):payload['sweep_code']=error.sweep_code
    return payload


def execute_one(exe,data,base,timeout,keep,verbose,temperature=None,parameters=None):
    report=dict(status='ERROR',checks=[],error=None)
    if parameters is not None:
        report.update(parameters=parameters,run_id=uuid.uuid4().hex,simulation_status='ERROR',checks_count=0,checks_status='NOT_RUN')
    if temperature is not None:
        report.update(temperature_c=temperature,temperature_method='OPTIONS TEMP',simulation_status='ERROR',checks_count=0,checks_status='NOT_RUN')
    try:
        if temperature is not None or parameters is not None:require_running_altium(exe)
        report['status'],report['checks']=run_altium(exe,data,base,timeout,keep,report,verbose)
        if report['status']=='ERROR':report['error']=next(r['error'] for r in report['checks'] if 'error' in r)
    except TestError as e:report['error']=error_payload(e)
    except (OSError,ValueError,subprocess.TimeoutExpired) as e:
        report['error']=dict(code='SIM_NOT_RUN',message=str(e))
    if temperature is not None or parameters is not None:
        report['checks_count']=len(report['checks'])
        report['checks_status']=('NOT_CONFIGURED' if not data['checks'] else ('NOT_RUN' if not report['checks'] else ('ERROR' if any('error' in c for c in report['checks']) else ('PASS' if all(c['passed'] for c in report['checks']) else 'FAIL'))))
        if temperature is not None and report['error']:report['error'].setdefault('temperature_code','TEMP_RESULT_MISSING' if report['error']['code'] in ('SDF_NOT_SAVED','SDF_PARSE_ERROR') else 'TEMP_RUN_FAILED')
        for graph in report.get('graphs',[]):
            if temperature is not None:graph['temperature_c']=temperature
            if parameters is not None:graph.update(run_id=report['run_id'],parameters=parameters)
        if parameters is not None and report['error']:report['error'].setdefault('sweep_code','SWEEP_RESULT_MISSING' if report['error']['code'] in ('SDF_NOT_SAVED','SDF_PARSE_ERROR') else 'SWEEP_RUN_FAILED')
    return report


def execute_test(exe,data,base,timeout,keep,verbose,report,max_runs=100):
    if 'sweep' in data:
        return execute_sweep(exe,data,base,timeout,keep,verbose,report,max_runs)
    plan=temperature_plan(data)
    if 'temperatures_c' not in data:
        report.update(execute_one(exe,data,base,timeout,keep,verbose,plan[0] if plan else None))
        return
    runs=[]
    for t in plan:
        variant=dict(data)
        variant.pop('temperatures_c')
        lines=data['spice'].splitlines();lines.insert(1,'.TEMP '+str(t))
        variant['spice']='\n'.join(lines)
        variant['checks']=[c for c in data['checks'] if 'temperature_c' not in c or c['temperature_c']==t]
        if 'output' in data:
            variant['output']=dict(data['output'],graphs=[{k:v for k,v in g.items() if k!='temperatures_c'} for g in data['output'].get('graphs',[]) if 'temperatures_c' not in g or t in g['temperatures_c']])
        runs.append(execute_one(exe,variant,base,timeout,keep,verbose,t))
    report.pop('checks',None)
    report['runs']=runs
    report['summary']=dict(total=len(runs),passed=sum(r['status']=='PASS' for r in runs),failed=sum(r['status']=='FAIL' for r in runs),errors=sum(r['status']=='ERROR' for r in runs))
    report['status']='ERROR' if report['summary']['errors'] else ('FAIL' if report['summary']['failed'] else 'PASS')
    report['error']=next((r['error'] for r in runs if r['error']),None)


from decimal import Decimal, localcontext
from itertools import product


class SweepInputError(TestError):
    def __init__(self,reason,message):
        super().__init__('INPUT_INVALID',message)
        self.sweep_code=reason


def sweep_number(value):
    try:valid=finite_number(value)
    except OverflowError:valid=False
    if not valid:
        raise SweepInputError('SWEEP_INVALID','Sweep values must be finite numbers, not text or booleans')
    return Decimal(str(value))


def parameter_axes(data):
    placeholders=re.findall(r'\{\{([A-Za-z_][A-Za-z0-9_]*)\}\}',data['spice'])
    if 'sweep' not in data:
        if '{{' in data['spice'] or '}}' in data['spice']:
            raise SweepInputError('SWEEP_PLACEHOLDER_MISSING','Unresolved parameter placeholder without sweep')
        axes={}
    else:
        sweep=data['sweep']
        if not isinstance(sweep,dict) or set(sweep)!={'parameters'} or not isinstance(sweep['parameters'],dict):
            raise SweepInputError('SWEEP_INVALID','sweep must contain a parameters object')
        if not sweep['parameters']:raise SweepInputError('SWEEP_EMPTY','No sweep parameters')
        axes={}
        for name,spec in sweep['parameters'].items():
            if not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*',name) or not isinstance(spec,dict):
                raise SweepInputError('SWEEP_INVALID','Invalid parameter name or definition')
            if name not in placeholders:raise SweepInputError('SWEEP_PARAMETER_UNUSED','Parameter is unused: '+name)
            if set(spec)=={'values'}:
                if not isinstance(spec['values'],list) or not spec['values']:
                    raise SweepInputError('SWEEP_EMPTY','Empty values for '+name)
                values=[sweep_number(v) for v in spec['values']]
                if len(set(values))!=len(values):raise SweepInputError('SWEEP_DUPLICATE_VALUE','Duplicate values for '+name)
                axes[name]=dict(values=values,count=len(values))
            elif set(spec)=={'start','stop','step'}:
                start,stop,step=(sweep_number(spec[k]) for k in ('start','stop','step'))
                if step==0 or (stop-start)*step<0:
                    raise SweepInputError('SWEEP_INVALID_RANGE','Zero step or wrong direction for '+name)
                with localcontext() as ctx:
                    ctx.prec=1000
                    count=int((stop-start)/step)+1
                axes[name]=dict(start=start,step=step,count=count)
            else:raise SweepInputError('SWEEP_INVALID_RANGE','Use values or start/stop/step, exclusively: '+name)
        unknown=set(placeholders)-set(axes)
        residual=re.sub(r'\{\{[A-Za-z_][A-Za-z0-9_]*\}\}','',data['spice'])
        if unknown or '{{' in residual or '}}' in residual:
            raise SweepInputError('SWEEP_PLACEHOLDER_MISSING','Unknown or malformed placeholders: '+', '.join(sorted(unknown)))
    return axes


def axis_value(axis,index):
    if 'values' in axis:return axis['values'][index]
    with localcontext() as ctx:
        ctx.prec=1000
        return axis['start']+index*axis['step']


def condition_indices(condition,axes):
    if not isinstance(condition,dict):raise SweepInputError('SWEEP_CHECK_INVALID','when must be an object')
    result={}
    for name,value in condition.items():
        if name not in axes:raise SweepInputError('SWEEP_CHECK_INVALID','Unknown parameter in when: '+name)
        try:target=sweep_number(value)
        except SweepInputError as e:raise SweepInputError('SWEEP_CHECK_INVALID',e.message) from e
        axis=axes[name]
        if 'values' in axis:candidates=range(axis['count'])
        else:
            with localcontext() as ctx:
                ctx.prec=1000
                near=int((target-axis['start'])/axis['step'])
            candidates=[j for j in (near-1,near,near+1) if 0<=j<axis['count']]
        exact=[j for j in candidates if axis_value(axis,j)==target]
        if exact:result[name]=exact[0];continue
        close=[j for j in candidates if math.isclose(float(axis_value(axis,j)),float(target),rel_tol=1e-12,abs_tol=0)]
        if len(close)!=1:raise SweepInputError('SWEEP_CHECK_INVALID','when value absent or ambiguous for '+name)
        result[name]=close[0]
    return result


def sweep_plan(data,max_runs=100,base=None):
    axes=parameter_axes(data)
    temps=temperature_plan(data)
    total=math.prod(a['count'] for a in axes.values())*(len(temps) if 'temperatures_c' in data else 1)
    if isinstance(max_runs,bool) or not isinstance(max_runs,int) or max_runs<1:
        raise SweepInputError('SWEEP_INVALID','max-runs must be a positive integer')
    if 'sweep' in data and total>max_runs:
        raise SweepInputError('SWEEP_TOO_MANY_RUNS',f'Sweep requires {total} runs; limit is {max_runs}')
    for request in data.get('checks',[]):
        if isinstance(request,dict) and 'when' in request:condition_indices(request['when'],axes)
    output=data.get('output',{})
    if isinstance(output,dict) and isinstance(output.get('graphs',[]),list):
        for request in output.get('graphs',[]):
            if isinstance(request,dict) and 'when' in request:condition_indices(request['when'],axes)
    # Validate every rendered scheme before any simulator invocation. The run cap bounds this work.
    if 'sweep' in data:
        for name,axis in axes.items():
            floats=[float(axis_value(axis,j)) for j in range(axis['count'])]
            if any(not math.isfinite(v) for v in floats) or len(set(floats))!=len(floats):
                raise SweepInputError('SWEEP_DUPLICATE_VALUE','Grid cannot be represented distinctly as SPICE numbers: '+name)
        for indices in product(*(range(a['count']) for a in axes.values())):
            parameters={n:axis_value(a,j) for (n,a),j in zip(axes.items(),indices)}
            variant=dict(data,spice=render_parameters(data['spice'],parameters))
            variant.pop('sweep')
            generate_nsx(variant,base or Path('.'))
    return axes,total


def render_parameters(spice,parameters):
    rendered=re.sub(r'\{\{([A-Za-z_][A-Za-z0-9_]*)\}\}',lambda m:str(parameters[m[1]]),spice)
    if '{{' in rendered or '}}' in rendered:raise SweepInputError('SWEEP_PLACEHOLDER_MISSING','Unresolved placeholder')
    return rendered


def execute_sweep(exe,data,base,timeout,keep,verbose,report,max_runs):
    axes,total=sweep_plan(data,max_runs,base)
    temperatures=temperature_plan(data) or [None]
    runs=[];stopped=False
    for t in temperatures:
        for combo in product(*(range(a['count']) for a in axes.values())):
            if stopped:break
            indices=dict(zip(axes,combo))
            decimals={n:axis_value(axes[n],j) for n,j in indices.items()}
            parameters={n:float(v) for n,v in decimals.items()}
            def applies(request):
                return all(indices[n]==j for n,j in condition_indices(request.get('when',{}),axes).items())
            variant=dict(data,spice=render_parameters(data['spice'],decimals))
            variant.pop('sweep');variant.pop('temperatures_c',None)
            if 'temperatures_c' in data:
                lines=variant['spice'].splitlines();lines.insert(1,'.TEMP '+str(t));variant['spice']='\n'.join(lines)
            variant['checks']=[{k:v for k,v in c.items() if k!='when'} for c in data['checks'] if applies(c) and ('temperature_c' not in c or c['temperature_c']==t)]
            if 'output' in data:
                variant['output']=dict(data['output'],graphs=[{k:v for k,v in g.items() if k not in ('when','temperatures_c')} for g in data['output'].get('graphs',[]) if applies(g) and ('temperatures_c' not in g or t in g['temperatures_c'])])
            run=execute_one(exe,variant,base,timeout,keep,verbose,t,parameters)
            runs.append(run)
            if run['error'] and run['error']['code'] in ('TIMEOUT','SCRIPT_NOT_EXECUTED','BUSY','ALTIUM_NOT_RUNNING','ALTIUM_STATE_UNKNOWN'):
                stopped=True
        if stopped:break
    report.pop('checks',None);report['runs']=runs
    report['summary']=dict(total=total,passed=sum(r['status']=='PASS' for r in runs),failed=sum(r['status']=='FAIL' for r in runs),errors=sum(r['status']=='ERROR' for r in runs))
    if stopped:report['summary'].update(completed=len(runs),not_run=total-len(runs))
    report['status']='ERROR' if report['summary']['errors'] else ('FAIL' if report['summary']['failed'] else 'PASS')
    report['error']=next((r['error'] for r in runs if r['error']),None)


class JSONArgumentParser(argparse.ArgumentParser):
    def error(self, message):
        raise TestError('INPUT_INVALID',message)

def main():
    p = JSONArgumentParser()
    p.add_argument('test')
    p.add_argument('--altium', default=os.environ.get('DBUR_ALTIUM'))
    p.add_argument('--timeout', type=float, default=120)
    p.add_argument('--keep-files', action='store_true')
    p.add_argument('--verbose', action='store_true')
    p.add_argument('--max-runs',type=int,default=100)
    report = dict(status='ERROR', test=None, simulator='Altium MixedSim', checks=[], error=None)
    try:
        args = p.parse_args()
        data = load_test(args.test,args.max_runs)
        report['test'] = data['name']
        if not math.isfinite(args.timeout) or args.timeout <= 0:
            raise TestError('INPUT_INVALID', 'timeout must be positive and finite')
        if not args.altium:
            candidate=Path(os.environ.get('ProgramFiles','C:/Program Files'))/'Altium/AD24/X2.EXE'
            if candidate.is_file():
                version=subprocess.run(['powershell','-NoProfile','-Command', "(Get-Item -LiteralPath '"+str(candidate).replace("'","''")+"').VersionInfo.ProductVersion"],capture_output=True,text=True,timeout=10).stdout.strip()
                if version.startswith('24.4.'):
                    args.altium=str(candidate)
        if not args.altium or not Path(args.altium).is_file():
            raise TestError('ALTIUM_NOT_FOUND', 'Set --altium or DBUR_ALTIUM to X2.EXE')
        require_running_altium(args.altium)
        execute_test(args.altium,data,Path(args.test).resolve().parent,args.timeout,args.keep_files,args.verbose,report,args.max_runs)
    except (OSError, ValueError, subprocess.TimeoutExpired) as e:
        report['error'] = dict(code='SIM_NOT_RUN', message=str(e))
    except TestError as e:
        report['error'] = error_payload(e)
    print(json.dumps(report, ensure_ascii=True, allow_nan=False, separators=(',', ':')))
    return {'PASS':0,'FAIL':1,'ERROR':2}[report['status']]

if __name__ == '__main__':
    sys.exit(main())
