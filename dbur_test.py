"""DBUR Test: input preparation and independent result validation.
AC impedance, transient/DC checks and numeric graph export.
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

def load_test(path):
    try:
        data = json.loads(Path(path).read_text(encoding='utf-8-sig'), parse_constant=lambda x: (_ for _ in ()).throw(ValueError('Nonstandard JSON number: '+x)))
        if not isinstance(data, dict):
            raise ValueError('Expected an object')
        for key in ('name', 'spice'):
            if not isinstance(data.get(key), str) or not data[key].strip():
                raise ValueError(f'{key} must be a nonempty string')
        if not isinstance(data.get('checks'), list) or not data['checks']:
            raise ValueError('checks must be a nonempty array')
        for c in data['checks']:
            if not isinstance(c, dict):
                raise ValueError('Each check must be an object')
            if c.get('analysis') == 'ac':
                validate_ac_request(c, check=True)
            else:
                if not isinstance(c.get('signal'), str) or not c['signal'].strip():
                    raise ValueError('Invalid signal')
                if c.get('analysis') not in (None, 'tran', 'dc'):
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
    if not re.search(r'^\s*\.(TRAN|AC|DC)\b', '\n'.join(lines[1:]), re.I | re.M):
        raise ValueError('Requires .TRAN, .AC or .DC after the title line; .OP is unverified')
    ends = [i for i, line in enumerate(lines) if re.fullmatch(r'\s*\.END\s*', line, re.I)]
    if len(ends) > 1 or (ends and any(s.strip() and not s.lstrip().startswith('*') for s in lines[ends[0]+1:])):
        raise ValueError('Invalid .END position')
    includes = [f'.include "{model_path(base / m)}"' for m in data.get('models', [])]
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
            if r.get('analysis') not in (None,'ac','tran','dc'):raise ValueError('Invalid graph analysis')
        else:raise ValueError('Unknown graph type')
        cs=r.get('components',['real'])
        if not isinstance(cs,list) or not cs or any(c not in COMPONENTS for c in cs) or len(cs)!=len(set(cs)):
            raise ValueError('Invalid graph components')
        m=r.get('max_points')
        if 'max_points' in r and (isinstance(m,bool) or not isinstance(m,int) or m<2):
            raise ValueError('max_points must be an integer >= 2 to preserve endpoints')


def chart_analysis(chart):
    return {'transient analysis':'tran','ac analysis':'ac','dc sweep':'dc'}.get(chart.name.casefold())


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
        xunit='Hz' if analysis=='ac' else 's'
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
               "-ErrorAction Stop | Select-Object ExecutablePath,CommandLine | ConvertTo-Json -Compress")
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
    report = dict(status='ERROR', test=None, simulator='Altium MixedSim', checks=[], error=None)
    try:
        args = p.parse_args()
        data = load_test(args.test)
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
        report['status'], report['checks'] = run_altium(args.altium, data, Path(args.test).resolve().parent, args.timeout, args.keep_files, report, args.verbose)
        if report['status'] == 'ERROR':
            report['error'] = next(r['error'] for r in report['checks'] if 'error' in r)
    except (OSError, ValueError, subprocess.TimeoutExpired) as e:
        report['error'] = dict(code='SIM_NOT_RUN', message=str(e))
    except TestError as e:
        report['error'] = dict(code=e.code, message=e.message)
    print(json.dumps(report, ensure_ascii=True, allow_nan=False, separators=(',', ':')))
    return {'PASS':0,'FAIL':1,'ERROR':2}[report['status']]

if __name__ == '__main__':
    sys.exit(main())
