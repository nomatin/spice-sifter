"""DBUR Test: input preparation and independent result validation.
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
            if not isinstance(c, dict) or not isinstance(c.get('signal'), str) or not c['signal'].strip():
                raise ValueError('Invalid signal')
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
    if not re.search(r'^\s*\.TRAN\b', '\n'.join(lines[1:]), re.I | re.M):
        raise ValueError('MVP requires .TRAN after the title line; .OP is unverified')
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
                if wm.get('WaveIsXComplex') != 'FALSE' or wm.get('WaveIsYComplex') != 'FALSE':
                    raise TestError('UNSUPPORTED_FORMAT', 'Complex binary layout has not been verified')
                size = 16*n
                if pos + size > len(data):
                    raise ValueError('Truncated double block')
                values = list(struct.iter_unpack('<dd', data[pos:pos+size]))
                pos += size
                consume(b'\nWaveDataStart\n')
                size = 8*n
                if pos + size > len(data):
                    raise ValueError('Truncated single block')
                pos += size
                consume(b'\n')
                chart.waves.append(SDFWave(wave_name, wm.get('WaveYUnitStr',''),wm.get('WaveVariableType',''),n,[v[0] for v in values],[v[1] for v in values]))
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
                    charts=[c for c in doc.charts if c.name.casefold()=='transient analysis']
                    if len(charts)!=1:
                        raise TestError('UNSUPPORTED_FORMAT','Expected one unambiguous Transient Analysis chart')
                    waves={}
                    for wave in charts[0].waves:
                        if wave.name in waves:
                            raise TestError('UNSUPPORTED_FORMAT','Duplicate wave name')
                        waves[wave.name]=wave.y
                    success=True
                    return validate(waves,data['checks'])
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
