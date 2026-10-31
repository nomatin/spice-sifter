"""Spice Sifter: input preparation and independent result validation.
Automatic measurements, parameter/temperature series, AC impedance and graph export.
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
        if not isinstance(data.get('checks'), list) or (not data['checks'] and plan is None and 'sweep' not in data and not data.get('measurements')):
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
        validate_measurements(data)
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
    try:return not isinstance(v, bool) and isinstance(v, (int, float)) and math.isfinite(v)
    except OverflowError:return False


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
    root = Path(tempfile.gettempdir()) / 'SpiceSifter'
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
        raise TestError('BUSY','Another Spice Sifter run holds the Altium lock')
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
    if 'temperature_c' in report or 'parameters' in report or '_measurement_requests' in report:report['run_id']=work.name
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
                    if data.get('measurements'):process_measurements(doc,data['measurements'],report)
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
    if data.get('measurements'):
        report.update(run_id=uuid.uuid4().hex,_measurement_requests=data['measurements'])
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
        finish_measurements(data,report)
        return
    runs=[]
    for t in plan:
        variant=dict(data)
        variant.pop('temperatures_c')
        lines=data['spice'].splitlines();lines.insert(1,'.TEMP '+str(t))
        variant['spice']='\n'.join(lines)
        variant['checks']=[c for c in data['checks'] if 'temperature_c' not in c or c['temperature_c']==t]
        if 'measurements' in data:variant['measurements']=[m for m in data['measurements'] if 'temperature_c' not in m or m['temperature_c']==t]
        if 'output' in data:
            variant['output']=dict(data['output'],graphs=[{k:v for k,v in g.items() if k!='temperatures_c'} for g in data['output'].get('graphs',[]) if 'temperatures_c' not in g or t in g['temperatures_c']])
        runs.append(execute_one(exe,variant,base,timeout,keep,verbose,t))
    report.pop('checks',None)
    report['runs']=runs
    report['summary']=dict(total=len(runs),passed=sum(r['status']=='PASS' for r in runs),failed=sum(r['status']=='FAIL' for r in runs),errors=sum(r['status']=='ERROR' for r in runs))
    report['status']='ERROR' if report['summary']['errors'] else ('FAIL' if report['summary']['failed'] else 'PASS')
    report['error']=next((r['error'] for r in runs if r['error']),None)
    finish_measurements(data,report)


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
            if 'measurements' in data:variant['measurements']=[m for m in data['measurements'] if applies(m) and ('temperature_c' not in m or m['temperature_c']==t)]
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
    finish_measurements(data,report)


MEASUREMENT_TYPES={'at','crossing','mean','rms','peak_to_peak','ripple','rise_time','fall_time','delay','settling_time','cutoff_3db','resonant_frequency','capacitance_series','inductance_series','esr','sweep_crossing','rds_on'}


def merror(code,message):
    raise TestError('MEASUREMENT_'+code,message)


def validate_measurements(data):
    requests=data.get('measurements',[])
    if not isinstance(requests,list):merror('INVALID','measurements must be an array')
    ids=set();axes=parameter_axes(data)
    for r in requests:
        if not isinstance(r,dict) or not isinstance(r.get('id'),str) or not r['id'].strip() or r['id'] in ids:merror('INVALID','Measurement ids must be unique nonempty strings')
        ids.add(r['id'])
        if r.get('type') not in MEASUREMENT_TYPES:merror('UNSUPPORTED','Unknown measurement type')
        if 'when' in r:condition_indices(r['when'],axes)
        if 'temperature_c' in r:
            if temperature_plan(data) is None or temperature_value(r['temperature_c']) not in temperature_plan(data):merror('INVALID','Measurement temperature absent from test')
        if r.get('analysis') not in (None,'tran','dc','op','ac'):merror('INVALID','Invalid measurement analysis')
        if r.get('occurrence','first') not in ('first','last'):merror('INVALID','occurrence must be first or last')
        if r.get('edge','either') not in ('rising','falling','either'):merror('INVALID','Invalid edge')
        if 'limits' in r:
            limits=r['limits']
            if not isinstance(limits,dict) or not limits or set(limits)-{'min','max'}:merror('INVALID','limits must contain min and/or max')
            if any(not finite_number(v) for v in limits.values()) or limits.get('min',-math.inf)>limits.get('max',math.inf):merror('INVALID','Invalid limits')
        if 'window' in r:
            w=r['window']
            if not isinstance(w,dict) or set(w)!={'start','stop'} or not all(finite_number(v) for v in w.values()) or w['stop']<=w['start']:merror('INVALID_WINDOW','window needs finite start < stop')
        typ=r['type']
        required={'at':['signal','x'],'crossing':['signal','level'],'mean':['signal'],'rms':['signal'],'peak_to_peak':['signal'],'ripple':['signal','window'],'rise_time':['signal','low','high'],'fall_time':['signal','low','high'],'delay':['trigger','target'],'settling_time':['signal','target_value','start','stop'],'cutoff_3db':['input','output','reference_frequency_hz'],'resonant_frequency':['voltage','current','current_sign'],'capacitance_series':['voltage','current','current_sign','frequency_hz'],'inductance_series':['voltage','current','current_sign','frequency_hz'],'esr':['voltage','current','current_sign','frequency_hz'],'sweep_crossing':['parameter','signal','level'],'rds_on':['voltage','current','current_sign']}[typ]
        if any(k not in r for k in required):merror('INVALID','Missing fields for '+typ+': '+', '.join(required))
        for k in ('x','level','low','high','from_percent','to_percent','reference_frequency_hz','frequency_hz','frequency_min_hz','frequency_max_hz','target_value','start','stop','tolerance_abs','tolerance_rel','min_reactance_ohm','min_current_a','current_sign'):
            if k in r and not finite_number(r[k]):merror('INVALID',k+' must be finite')
        for k in ('signal','voltage','current','input','output','parameter'):
            if k in r and (not isinstance(r[k],str) or not r[k].strip()):merror('INVALID',k+' must name a signal or parameter')
        for key in ('parameter_unit','x_unit'):
            if key in r and (not isinstance(r[key],str) or not r[key].strip()):merror('INVALID',key+' must be a nonempty string')
        if 'markers' in r and not isinstance(r['markers'],bool):merror('INVALID','markers must be boolean')
        if 'current_sign' in r and r['current_sign'] not in (-1,1):merror('INVALID','current_sign must be 1 or -1')
        if r.get('frequency_sampling','interpolate') not in ('exact','interpolate'):merror('INVALID','Invalid frequency sampling')
        for k in ('frequency_hz','reference_frequency_hz','frequency_min_hz','frequency_max_hz','min_current_a','min_reactance_ohm'):
            if k in r and r[k]<=0:merror('INVALID',k+' must be positive')
        if typ in ('rise_time','fall_time'):
            first=r.get('from_percent',10 if typ=='rise_time' else 90);last=r.get('to_percent',90 if typ=='rise_time' else 10)
            if r['high']<=r['low'] or not (0<=first<last<=100 if typ=='rise_time' else 0<=last<first<=100):merror('INVALID','Invalid transition levels or percentages')
        if typ=='delay':
            for event in ('trigger','target'):
                e=r[event]
                if not isinstance(e,dict) or not isinstance(e.get('signal'),str) or not finite_number(e.get('level')) or e.get('edge','rising') not in ('rising','falling','either'):merror('INVALID','Invalid delay event')
        if typ=='settling_time':
            ts=[k for k in ('tolerance_abs','tolerance_rel') if k in r]
            if len(ts)!=1 or r[ts[0]]<=0 or r['stop']<=r['start'] or (ts[0]=='tolerance_rel' and r['target_value']==0):merror('INVALID','Specify exactly one positive tolerance and start < stop')
        if r.get('frequency_axis','auto') not in ('auto','linear','log'):merror('INVALID','Invalid frequency_axis')
        if typ=='cutoff_3db' and 'window' in r and r['window']['start']<=0:merror('INVALID_WINDOW','AC frequencies must be positive')
        if 'frequency_min_hz' in r and 'frequency_max_hz' in r and r['frequency_min_hz']>=r['frequency_max_hz']:merror('INVALID_WINDOW','Frequency bounds must increase')
        if typ=='cutoff_3db' and r.get('direction','high') not in ('high','low'):merror('INVALID','Invalid cutoff direction')
        if typ=='resonant_frequency' and r.get('method','minimum_impedance')!='minimum_impedance':merror('UNSUPPORTED','Unknown resonance method')
        if typ=='sweep_crossing':
            if r['parameter'] not in axes:merror('INVALID','Unknown sweep axis')
            if r['parameter'] in r.get('when',{}):merror('SWEEP_AMBIGUOUS','when must not fix measured sweep axis')
            if r.get('reduction','last') not in ('first','last','min','max'):merror('INVALID','Invalid sweep reduction')


def mcurve(x,y):
    if len(x)!=len(y) or not x:merror('NOT_ENOUGH_POINTS','Empty or mismatched waveform')
    xx=[];yy=[]
    for a,b in zip(x,y):
        if isinstance(a,complex) or isinstance(b,complex):merror('INVALID','Expected real waveform')
        if not math.isfinite(a) or not math.isfinite(b):merror('NON_FINITE','Nonfinite waveform')
        if xx and a<xx[-1]:merror('INVALID_AXIS','Axis must increase')
        if xx and a==xx[-1]:
            if b!=yy[-1]:merror('INVALID_AXIS','Duplicate X has conflicting Y')
            continue
        xx.append(a);yy.append(b)
    return xx,yy


def minterp(x,y,f):
    import bisect
    if f<x[0] or f>x[-1]:merror('OUT_OF_RANGE','Requested coordinate outside data')
    j=bisect.bisect_left(x,f)
    if j<len(x) and x[j]==f:return y[j]
    return y[j-1]+(y[j]-y[j-1])*(f-x[j-1])/(x[j]-x[j-1])


def mwindow(x,y,window=None):
    if window is None:return x,y
    a,b=window['start'],window['stop']
    if a<x[0] or b>x[-1] or a>=b:merror('INVALID_WINDOW','Window outside waveform')
    indices=[j for j,v in enumerate(x) if a<v<b]
    return [a]+[x[j] for j in indices]+[b],[minterp(x,y,a)]+[y[j] for j in indices]+[minterp(x,y,b)]


def mcrossings(x,y,level,edge='either'):
    events=[];j=0
    if len(x)>1 and y[0]==level:
        k=0
        while k+1<len(x) and y[k+1]==level:k+=1
        if k+1<len(x):
            direction='rising' if y[k+1]>level else 'falling'
            if edge in ('either',direction):
                if k>0:merror('AMBIGUOUS_CROSSING','Initial threshold plateau is ambiguous')
                events.append(x[0])
    while j<len(x)-1:
        a,b=y[j]-level,y[j+1]-level
        if a!=0 and b==0:
            k=j+1
            while k+1<len(x) and y[k+1]==level:k+=1
            after=y[k+1]-level if k+1<len(x) else -a
            if a*after<0:
                if k>j+1:merror('AMBIGUOUS_CROSSING','Threshold plateau has no unique crossing coordinate')
                direction='rising' if a<0 else 'falling'
                if edge in ('either',direction):events.append(x[j+1])
            j=k
        elif a*b<0:
            direction='rising' if a<0 else 'falling'
            if edge in ('either',direction):events.append(x[j]+(x[j+1]-x[j])*(-a)/(b-a))
        j+=1
    return events


def mselect(events,occurrence='first',ambiguous=False):
    if not events:merror('NO_CROSSING','No matching crossing')
    if ambiguous and len(events)>1:merror('AMBIGUOUS_CROSSING','Multiple crossings; specify occurrence or narrow window')
    return events[-1] if occurrence=='last' else events[0]


def measurement_wave(doc,r,name=None,analysis=None):
    try:
        chart=select_chart(doc,analysis or r.get('analysis','tran'),[name or r['signal']])
        w=find_wave(chart,name or r['signal'])
    except TestError as e:raise TestError('MEASUREMENT_SIGNAL_MISSING',e.message) from e
    x,y=mcurve(w.x,w.y)
    xunit={'tran':'s','dc':None,'op':'1'}.get(chart_analysis(chart))
    if chart_analysis(chart)=='dc':
        units={v.unit for v in chart.waves if v.x==v.y and v.x==w.x and v.unit}
        xunit=r.get('x_unit') or (next(iter(units)) if len(units)==1 else None)
        if xunit is None:merror('INVALID_AXIS','Cannot determine DC axis unit; specify x_unit')
    return x,y,w.unit,xunit


def measure_value(doc,r):
    typ=r['type'];markers={}
    if typ in ('capacitance_series','inductance_series','esr','resonant_frequency'):
        x,v,i=impedance_waves(doc,r)
        if typ=='resonant_frequency':
            z=[abs(impedance(a,b,r)) for a,b in zip(v,i)]
            start=max(r.get('frequency_min_hz',x[0]),r.get('window',{}).get('start',x[0]));stop=min(r.get('frequency_max_hz',x[-1]),r.get('window',{}).get('stop',x[-1]))
            x,z=mwindow(x,z,dict(start=start,stop=stop))
            indices=[j for j in range(1,len(x)-1) if z[j]<z[j-1] and z[j]<z[j+1]]
            if not indices:merror('NO_EXTREMUM','No internal impedance minimum')
            if len(indices)>1:merror('AMBIGUOUS_CROSSING','Multiple local impedance minima')
            value=x[indices[0]];markers={'frequency_hz':value};unit='Hz'
        else:
            f=r['frequency_hz'];mode=r.get('frequency_sampling','interpolate');z=impedance(sampled(x,v,f,mode),sampled(x,i,f,mode),r)
            if typ=='esr':value=z.real;unit='Ohm'
            else:
                minimum=r.get('min_reactance_ohm',1e-12)
                if abs(z.imag)<minimum:merror('ZERO_DIVISION','Reactive part is too small')
                if typ=='capacitance_series':
                    if z.imag>=0:merror('INVALID','Capacitance requires negative reactance')
                    value=-1/(2*math.pi*f*z.imag);unit='F'
                else:
                    if z.imag<=0:merror('INVALID','Inductance requires positive reactance')
                    value=z.imag/(2*math.pi*f);unit='H'
            markers={'frequency_hz':f}
    elif typ=='cutoff_3db':
        chart=select_chart(doc,'ac',[r['input'],r['output']]);a=find_wave(chart,r['input'],True);b=find_wave(chart,r['output'],True)
        x=frequency_axis(a)
        if x!=frequency_axis(b):merror('INVALID_AXIS','Transfer function axes differ')
        if any(abs(v)<1e-15 for v in a.y):merror('ZERO_DIVISION','Input voltage too small')
        h=[abs(out/inn) for out,inn in zip(b.y,a.y)]
        if any(not math.isfinite(v) for v in h):merror('NON_FINITE','Nonfinite transfer magnitude')
        if any(v<=0 for v in h):merror('INVALID','Zero transfer magnitude cannot be interpolated in dB')
        axis_mode=r.get('frequency_axis','auto')
        if axis_mode=='auto':
            if len(x)<3:merror('NOT_ENOUGH_POINTS','Need three frequencies to infer grid or specify frequency_axis')
            linear=all(math.isclose(b-a,x[1]-x[0],rel_tol=1e-7) for a,b in zip(x,x[1:]))
            logarithmic=all(math.isclose(math.log(b/a),math.log(x[1]/x[0]),rel_tol=1e-7) for a,b in zip(x,x[1:]))
            if not linear and not logarithmic:merror('INVALID_AXIS','Irregular AC grid: specify frequency_axis')
        else:linear=axis_mode=='linear'
        coordinate=(lambda f:f) if linear else math.log
        inverse=(lambda f:f) if linear else math.exp
        logx=[coordinate(v) for v in x];db=[20*math.log10(v) for v in h];f=r['reference_frequency_hz'];reference=minterp(logx,db,coordinate(f));threshold=reference-10*math.log10(2)
        logx,db=mwindow(logx,db,{'start':coordinate(r['window']['start']),'stop':coordinate(r['window']['stop'])} if 'window' in r else None)
        direction=r.get('direction','high');events=mcrossings(logx,db,threshold,'falling' if direction=='high' else 'rising')
        events=[v for v in events if v>coordinate(f)] if direction=='high' else [v for v in events if v<coordinate(f)]
        selected=mselect(events,'first' if direction=='high' else 'last');value=inverse(selected);unit='Hz';markers={'reference_frequency_hz':f,'reference_db':reference,'frequency_hz':value}
    elif typ=='rds_on':
        analysis=r.get('analysis','op')
        if analysis not in ('dc','op'):merror('INVALID','RDS(on) requires DC or OP')
        paired=SDFDocument(doc.version,[select_chart(doc,analysis,[r['voltage'],r['current']])])
        x,v,vu,_=measurement_wave(paired,r,r['voltage'],analysis);ix,i,iu,_=measurement_wave(paired,r,r['current'],analysis)
        if vu!='V' or iu!='A':merror('INVALID','RDS(on) needs voltage in V and current in A')
        if x!=ix:merror('INVALID_AXIS','Voltage/current axes differ')
        if 'x' in r:v0=minterp(x,v,r['x']);i0=minterp(ix,i,r['x'])
        elif len(x)==1:v0=v[0];i0=i[0]
        else:merror('SWEEP_AMBIGUOUS','Choose x for a DC curve or use one OP point')
        if abs(i0)<r.get('min_current_a',1e-15):merror('ZERO_DIVISION','Drain current too small')
        value=v0/(r['current_sign']*i0);unit='Ohm'
    elif typ=='delay':
        trigger=r['trigger'];target=r['target']
        paired=SDFDocument(doc.version,[select_chart(doc,'tran',[trigger['signal'],target['signal']])])
        x,y,_,_=measurement_wave(paired,r,trigger['signal'],'tran');x,y=mwindow(x,y,r.get('window'))
        triggers=mcrossings(x,y,trigger['level'],trigger.get('edge','rising'))
        t=mselect(triggers,r.get('occurrence','first'));next_trigger=next((v for v in triggers if v>t),math.inf)
        tx,ty,_,_=measurement_wave(paired,r,target['signal'],'tran');tx,ty=mwindow(tx,ty,r.get('window'))
        targets=[v for v in mcrossings(tx,ty,target['level'],target.get('edge','rising')) if t<=v<next_trigger]
        end=mselect(targets);value=end-t;unit='s';markers={'trigger_x':t,'target_x':end}
    else:
        x,y,unit,xunit=measurement_wave(doc,r)
        if typ=='settling_time':window={'start':r['start'],'stop':r['stop']}
        else:window=r.get('window')
        x,y=mwindow(x,y,window)
        if typ=='at':value=minterp(x,y,r['x'])
        else:
            if len(x)<2:merror('NOT_ENOUGH_POINTS','Need at least two distinct points')
            if typ in ('mean','rms'):
                vals=[v*v for v in y] if typ=='rms' else y
                integral=math.fsum((b-a)*(u+v)/2 for a,b,u,v in zip(x,x[1:],vals,vals[1:]))/(x[-1]-x[0])
                value=math.sqrt(max(0,integral)) if typ=='rms' else integral
            elif typ in ('peak_to_peak','ripple'):value=max(y)-min(y)
            elif typ=='crossing':value=mselect(mcrossings(x,y,r['level'],r.get('edge','either')),r.get('occurrence','first'));unit=xunit;markers={'x':value,'y':r['level']}
            elif typ in ('rise_time','fall_time'):
                a=r['low']+(r['high']-r['low'])*r.get('from_percent',10 if typ=='rise_time' else 90)/100;b=r['low']+(r['high']-r['low'])*r.get('to_percent',90 if typ=='rise_time' else 10)/100
                edge='rising' if typ=='rise_time' else 'falling'
                starts=mcrossings(x,y,a,edge);ends=mcrossings(x,y,b,edge);pairs=[]
                for start in starts:
                    end=next((v for v in ends if v>=start),None)
                    if end is None:continue
                    xx,yy=mwindow(x,y,{'start':start,'stop':end})
                    sign=1 if edge=='rising' else -1
                    if all(sign*(v-u)>=0 for u,v in zip(yy,yy[1:])):pairs.append((start,end))
                pair=mselect(pairs,r.get('occurrence','first'));value=pair[1]-pair[0];unit='s';markers={'start_x':pair[0],'stop_x':pair[1]}
            elif typ=='settling_time':
                target=r['target_value'];tol=r.get('tolerance_abs',abs(target)*r.get('tolerance_rel',0));outside=[j for j,v in enumerate(y) if abs(v-target)>tol]
                if abs(y[-1]-target)>tol:merror('NO_CROSSING','Signal not settled before end')
                if not outside:value=0
                else:
                    j=outside[-1]
                    if j+2>=len(x):merror('NOT_ENOUGH_POINTS','Need more than one point confirming settled interval')
                    boundary=target+math.copysign(tol,y[j]-target);entry=x[j]+(x[j+1]-x[j])*(boundary-y[j])/(y[j+1]-y[j]);value=entry-r['start']
                unit='s';markers={'settled_x':r['start']+value}
            else:merror('UNSUPPORTED','Unsupported local measurement')
    if not finite_number(value):merror('NON_FINITE','Measurement produced nonfinite value')
    return value,unit,markers


def measurement_result(r,calculate,metadata=None):
    row=dict(id=r['id'],type=r['type'],value=None,unit=None,status='ERROR',**(metadata or {}))
    if 'limits' in r:row['limits']=r['limits']
    try:
        value,unit,markers=calculate();row.update(value=value,unit=unit,status='MEASURED')
        if 'limits' in r:
            _,checks=validate({'measurement':[value]},[dict(signal='measurement',measurement='last',min=r['limits'].get('min',-math.inf),max=r['limits'].get('max',math.inf))])
            row['status']='PASS' if checks[0]['passed'] else 'FAIL'
        if r.get('markers'):row['markers']=markers
    except (TestError,ValueError,OverflowError,ZeroDivisionError) as e:
        code=e.code if isinstance(e,TestError) else 'MEASUREMENT_NON_FINITE'
        mappings={'SIGNAL_NOT_FOUND':'MEASUREMENT_SIGNAL_MISSING','AC_WAVE_NOT_FOUND':'MEASUREMENT_SIGNAL_MISSING','AC_CHART_NOT_FOUND':'MEASUREMENT_SIGNAL_MISSING','AC_FREQUENCY_OUT_OF_RANGE':'MEASUREMENT_OUT_OF_RANGE','IMPEDANCE_DIVISION_BY_ZERO':'MEASUREMENT_ZERO_DIVISION','NON_FINITE_RESULT':'MEASUREMENT_NON_FINITE','AC_FREQUENCY_GRID_MISMATCH':'MEASUREMENT_INVALID_AXIS'}
        row['error']=dict(code=mappings.get(code,code),message=str(e))
    return row


def process_measurements(doc,requests,report):
    local=[];samples={}
    for r in requests:
        if r['type']=='sweep_crossing':
            def extract(r=r):
                x,y,unit,_=measurement_wave(doc,r,analysis=r.get('analysis','op'))
                _,checks=validate({'sample':y},[dict(signal='sample',measurement=r.get('reduction','last'),min=-math.inf,max=math.inf)])
                return checks[0]['value']*r.get('current_sign',1),unit,{}
            samples[r['id']]=measurement_result({k:v for k,v in r.items() if k not in ('limits','markers')},extract)
        else:local.append(measurement_result(r,lambda r=r:measure_value(doc,r)))
    if local:report['measurements']=local
    if samples:report['_sweep_samples']=samples


def finish_measurements(data,report):
    if not data.get('measurements'):return
    runs=report.get('runs',[report]);rows=[]
    for run in runs:
        local=run.pop('measurements',[])
        requested=run.get('_measurement_requests',[])
        completed={r['id'] for r in local}|set(run.get('_sweep_samples',{}))
        for request in requested:
            if request['type']!='sweep_crossing' and request['id'] not in completed:
                local.append(measurement_result(request,lambda:merror('SIGNAL_MISSING','Simulation result unavailable for measurement')))
        for row in local:
            for k in ('run_id','parameters','temperature_c'):
                if k in run:row[k]=run[k]
        rows.extend(local)
    for r in data['measurements']:
        if r['type']!='sweep_crossing':continue
        groups={}
        for run in runs:
            if r['id'] not in {m['id'] for m in run.get('_measurement_requests',[])}:continue
            groups.setdefault(run.get('temperature_c'),[]).append(run)
        if not groups:rows.append(measurement_result(r,lambda:merror('NOT_ENOUGH_POINTS','No sweep results'),dict(scope='sweep')))
        for t,group in groups.items():
            metadata=dict(scope='sweep',run_ids=[g['run_id'] for g in group])
            if t is not None:metadata['temperature_c']=t
            if r.get('when'):metadata['parameters']=r['when']
            def calculate(group=group,r=r):
                axes=parameter_axes(data)
                expected=math.prod(a['count'] for name,a in axes.items() if name not in r.get('when',{}))
                if len(group)!=expected:merror('NOT_ENOUGH_POINTS','Sweep family is incomplete')
                pairs=[]
                for run in group:
                    sample=run.get('_sweep_samples',{}).get(r['id'])
                    if sample is None:merror('SIGNAL_MISSING','A required sweep run has no measurement sample')
                    if sample['status']=='ERROR':merror('SIGNAL_MISSING',sample['error']['message'])
                    pairs.append((run['parameters'][r['parameter']],sample['value']))
                pairs.sort();x=[v[0] for v in pairs];y=[v[1] for v in pairs]
                if len(set(x))!=len(x):merror('SWEEP_AMBIGUOUS','Multiple runs for one sweep coordinate; add when filters')
                x,y=mcurve(x,y);x,y=mwindow(x,y,r.get('window'))
                if len(x)<2:merror('NOT_ENOUGH_POINTS','Sweep needs two coordinates')
                value=mselect(mcrossings(x,y,r['level'],r.get('edge','either')),r.get('occurrence','first'),'occurrence' not in r)
                return value,r.get('parameter_unit','V'),{'x':value,'y':r['level']}
            rows.append(measurement_result(r,calculate,metadata))
    for run in runs:
        run.pop('_sweep_samples',None);run.pop('_measurement_requests',None)
    report['measurements']=rows
    if any(r['status']=='ERROR' for r in rows):
        report['status']='ERROR'
        if report.get('error') is None:report['error']=next(r['error'] for r in rows if r['status']=='ERROR')
    elif report['status']=='PASS' and any(r['status']=='FAIL' for r in rows):report['status']='FAIL'


class JSONArgumentParser(argparse.ArgumentParser):
    def error(self, message):
        raise TestError('INPUT_INVALID',message)

def main():
    p = JSONArgumentParser()
    p.add_argument('test')
    p.add_argument('--altium', default=os.environ.get('SPICE_SIFTER_ALTIUM'))
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
            raise TestError('ALTIUM_NOT_FOUND', 'Set --altium or SPICE_SIFTER_ALTIUM to X2.EXE')
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
