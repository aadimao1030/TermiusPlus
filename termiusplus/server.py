#!/usr/bin/env python3
"""TermiusPlus: local SSH browser and route-aware rsync transfer manager."""
import argparse
import inspect
import collections
import hashlib
import json
import os
import re
import secrets
import shlex
import shutil
import signal
import statistics
import subprocess
import threading
import time
import urllib.parse
import tempfile
import tarfile
import posixpath
import atexit
from . import terminal_sessions
from . import transfer_history
from . import remote_tasks
from . import route_probe, scheduler
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from .filesystem import file_preview, file_operation, local_info
from .ssh import validate_route, ssh_args, ssh_env

BASE = Path(__file__).resolve().parent.parent
PACKAGE = Path(__file__).resolve().parent
WEB = BASE / "web"
TOKEN = os.environ.get('TERMIUSPLUS_TOKEN') or secrets.token_urlsafe(32)
LOCK = threading.RLock()
JOBS = {}
REMOTE_START_CHECK = None
STAGES = {}
STATE_ROOT = Path(os.environ.get('TERMIUSPLUS_STATE_DIR', str(BASE / '.termiusplus-state')))
STATE_ROOT.mkdir(mode=0o700, exist_ok=True)
HISTORY_PATH = STATE_ROOT / 'transfers.json'
REMOTE_LINKS_PATH = STATE_ROOT / 'remote-links.json'

def remote_links():
    try:
        links = json.loads(REMOTE_LINKS_PATH.read_text())
        return links if isinstance(links, list) else []
    except (OSError, ValueError):
        return []

class StageRoot:
    name = str(STATE_ROOT / 'staging')
STAGE_ROOT = StageRoot()
Path(STAGE_ROOT.name).mkdir(mode=0o700, exist_ok=True)
atexit.register(terminal_sessions.close_all)
REMOTE_SCRIPT = r'''
import os,sys,json,stat,platform,hashlib,subprocess
p=os.path.realpath(os.path.expanduser(sys.argv[1]))
try:
 machine=open('/etc/machine-id').read().strip()
except OSError:
 machine=platform.node()
s=os.stat(p)
identity=hashlib.sha256((machine+'\0'+p+'\0'+str(s.st_dev)+'\0'+str(s.st_ino)).encode()).hexdigest()
entries=[]
if len(sys.argv)<3:
 with os.scandir(p) as it:
  for e in it:
   try:
    st=e.stat(follow_symlinks=False)
    entries.append(dict(name=e.name,path=os.path.join(p,e.name),directory=e.is_dir(follow_symlinks=True),symlink=e.is_symlink(),size=st.st_size))
   except OSError: pass
data=dict(path=p,identity=identity,entries=entries)
if len(sys.argv)>2:
 try:
  data['rsyncVersion']=subprocess.run(['rsync','--version'],stdout=subprocess.PIPE,stderr=subprocess.PIPE,universal_newlines=True,timeout=5).stdout
 except (OSError,subprocess.TimeoutExpired):
  data['rsyncVersion']='unavailable'
print(json.dumps(data,ensure_ascii=True))
'''


def remote_info(route, path, identity_only=False):
    command = 'python3 -c ' + shlex.quote(REMOTE_SCRIPT) + ' ' + shlex.quote(path)
    if identity_only:
        command += ' identity'
    try:
        result = subprocess.run(ssh_args(route) + [route['host'], command], capture_output=True, timeout=60, env=ssh_env(route))
    except subprocess.TimeoutExpired as exc:
        raise ValueError('SSH 连接或远程目录读取超时（60 秒）。请检查远程登录是否需要额外确认，或稍后重试。') from exc
    if result.returncode:
        raise ValueError(result.stderr.decode(errors='replace').strip()[-1500:] or '远程目录读取失败')
    data = json.loads(result.stdout)
    data['entries'].sort(key=lambda e: (not e['directory'], e['name'].casefold()))
    return data


REMOTE_PREVIEW_SCRIPT = inspect.getsource(file_preview) + "\nimport json,sys\nprint(json.dumps(file_preview(sys.argv[1]),ensure_ascii=True))\n"


def remote_preview(route, path):
    command = 'python3 -c ' + shlex.quote(REMOTE_PREVIEW_SCRIPT) + ' ' + shlex.quote(path)
    result = subprocess.run(ssh_args(route) + [route['host'], command], capture_output=True, timeout=25, env=ssh_env(route))
    if result.returncode:
        raise ValueError(result.stderr.decode(errors='replace').strip()[-1500:] or '远程文件预览失败')
    return json.loads(result.stdout)


REMOTE_FILE_OPERATION_SCRIPT = inspect.getsource(file_operation) + "\nimport json,sys\nprint(json.dumps(file_operation(**json.loads(sys.argv[1])),ensure_ascii=True))\n"


def file_action(data):
    values = {key: data.get(key) for key in ('root', 'path', 'operation', 'name')}
    if not all(isinstance(values[key], str) and values[key] for key in ('root', 'path', 'operation')):
        raise ValueError('文件操作参数不完整')
    if data.get('side') == 'local':
        return file_operation(**values)
    if data.get('side') != 'remote':
        raise ValueError('文件位置不正确')
    route = validate_route(data['route'])
    command = 'python3 -c ' + shlex.quote(REMOTE_FILE_OPERATION_SCRIPT) + ' ' + shlex.quote(json.dumps(values))
    result = subprocess.run(ssh_args(route) + [route['host'], command], capture_output=True, timeout=25, env=ssh_env(route))
    if result.returncode:
        raise ValueError(result.stderr.decode(errors='replace').strip()[-1500:] or '远程文件操作失败')
    return json.loads(result.stdout)


def rsync_binary():
    candidates = [os.environ.get('TERMIUSPLUS_RSYNC'), '/opt/homebrew/bin/rsync', '/usr/local/bin/rsync', shutil.which('rsync')]
    for candidate in candidates:
        if not candidate or not os.path.isfile(candidate):
            continue
        output = subprocess.run([candidate, '--version'], capture_output=True, text=True).stdout
        if re.search(r'rsync\s+version\s+[3-9]\.', output):
            return candidate
    raise ValueError('需要 rsync 3.x。macOS 可执行 brew install rsync，然后重新启动。系统自带 openrsync 不支持本工具的进度与续传参数。')


def measure(route, cancel=None, direction='download'):
    return route_probe.measure(route, cancel, direction, ssh_args, ssh_env, stop_process)


def stop_process(process):
    if process and process.poll() is None:
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass


def should_switch(current, alternative):
    return alternative > max(current * 1.5, current + 128 * 1024)


def needs_route_check(samples, now, threshold, expected, last_check, switches, interval=60):
    recent = [s for s in samples if now - s[0] <= 25]
    if len(recent) < 5 or recent[-1][0] - recent[0][0] < 18 or now - recent[-1][0] >= 4:
        return False
    average = statistics.median(s[1] for s in recent)
    return average < max(threshold, expected * .6) and now - last_check > interval and switches < 6


def validate_items(items, flatten=False):
    if not isinstance(items, list) or not 1 <= len(items) <= 20000:
        raise ValueError('请选择要传输或打包的文件/文件夹')
    result = []
    for item in items:
        if not isinstance(item, str) or not item or '\0' in item or item.startswith('/'):
            raise ValueError('文件路径不正确')
        parts = item.split('/')
        if any(part in ('', '.', '..') for part in parts):
            raise ValueError('文件路径不能越出当前目录')
        if item not in result:
            result.append(item)
    # Drop redundant children when their parent is already included.
    result = [item for item in result if not any(item.startswith(parent + '/') for parent in result if parent != item)]
    if flatten:
        names = [posixpath.basename(item) for item in result]
        if len(names) != len(set(names)):
            raise ValueError('所选项目包含同名文件或文件夹，不能同时放入同一目标目录，请分开传输或先打包')
    return result


def create_manifest(items, flatten=False):
    items = validate_items(items, flatten)
    handle = tempfile.NamedTemporaryFile(prefix='termiusplus-list-', delete=False)
    try:
        handle.write(b'\0'.join(os.fsencode(item) for item in items) + b'\0')
        return handle.name
    finally:
        handle.close()


def archive_name(items):
    base = posixpath.basename(items[0]) if len(items) == 1 else 'Archive'
    return base + '-' + time.strftime('%Y%m%d-%H%M%S') + '-' + secrets.token_hex(3) + '.tar.gz'


def local_archive(root, items, cancelled=None, progress=None):
    root = Path(root).expanduser().resolve(strict=True)
    items = validate_items(items)
    for item in items:
        source = root / item
        # Preserve selected symlinks but reject traversing a symlinked parent.
        if not source.parent.resolve().is_relative_to(root):
            raise ValueError('所选文件的父目录越出当前目录')
        source.lstat()
    destination = root / archive_name(items)
    temporary = root / ('.' + destination.name + '.part')
    try:
        with temporary.open('xb') as output:
            with tarfile.open(fileobj=output, mode='w:gz', dereference=False) as archive:
                count = 0
                def check(member):
                    nonlocal count
                    if cancelled and cancelled.is_set():
                        raise ValueError('打包已取消')
                    count += 1
                    if progress and count % 100 == 0:
                        progress('已打包 %d 个条目' % count)
                    return member
                class Reader:
                    def __init__(self, stream):
                        self.stream = stream
                    def read(self, size=-1):
                        if cancelled and cancelled.is_set():
                            raise ValueError('打包已取消')
                        return self.stream.read(size)
                def add(source, name):
                    member = check(archive.gettarinfo(str(source), arcname=name))
                    if member.isfile():
                        with source.open('rb') as content:
                            archive.addfile(member, Reader(content))
                    else:
                        archive.addfile(member)
                    if member.isdir():
                        for child in sorted(source.iterdir()):
                            add(child, name + '/' + child.name)
                for item in items:
                    add(root / item, item)
        if cancelled and cancelled.is_set():
            raise ValueError('打包已取消')
        # Publish without replacing an existing file.
        os.link(temporary, destination)
        temporary.unlink()
        return str(destination)
    finally:
        if temporary.exists():
            temporary.unlink()


REMOTE_ARCHIVE_SCRIPT = r"""
import os,sys,json,tarfile,signal
def interrupted(signum,frame):
 raise KeyboardInterrupt('Archive cancelled')
signal.signal(signal.SIGTERM,interrupted)
signal.signal(signal.SIGHUP,interrupted)
root=os.path.realpath(os.path.expanduser(sys.argv[1]))
items=json.loads(sys.argv[2]); name=sys.argv[3]
destination=os.path.join(root,name); temporary=os.path.join(root,'.'+name+'.part')
for item in items:
 if item.startswith('/') or any(p in ('','.','..') for p in item.split('/')) or '\0' in item:
  raise ValueError('Invalid archive path')
 p=os.path.join(root,item)
 if os.path.commonpath([root,os.path.realpath(os.path.dirname(p))]) != root:
  raise ValueError('Symlinked parent escapes directory')
 os.lstat(p)
try:
 with open(temporary,'xb') as output:
  with tarfile.open(fileobj=output,mode='w:gz',dereference=False) as archive:
   for item in items: archive.add(os.path.join(root,item),arcname=item)
 os.link(temporary,destination)
 os.unlink(temporary)
 print(json.dumps({'path':destination,'name':name}))
finally:
 if os.path.exists(temporary): os.unlink(temporary)
"""


def stage_create():
    identifier = secrets.token_hex(16)
    root = Path(STAGE_ROOT.name) / identifier
    root.mkdir()
    with LOCK:
        STAGES[identifier] = {'root': root, 'files': set(), 'sealed': False, 'busy': False, 'lock': threading.Lock()}
    return identifier


def stage_write(identifier, path, offset, content, directory=False):
    stage = STAGES.get(identifier)
    if not stage:
        raise ValueError('拖放暂存已失效，请重新拖入')
    validate_items([path])
    if len(content) > 4 * 1024 * 1024 or offset < 0:
        raise ValueError('暂存分块不正确')
    with stage['lock']:
        if stage['sealed']:
            raise ValueError('暂存已封存')
        target = stage['root'] / path
        if directory:
            target.mkdir(parents=True, exist_ok=True)
            return
        target.parent.mkdir(parents=True, exist_ok=True)
        if path not in stage['files']:
            if offset != 0:
                raise ValueError('分块必须从 0 开始')
            target.touch(exist_ok=False)
            stage['files'].add(path)
        if target.stat().st_size != offset:
            raise ValueError('分块顺序不正确，请重新拖入')
        with target.open('ab') as output:
            output.write(content)


def stage_seal(identifier):
    stage = STAGES.get(identifier)
    if not stage:
        raise ValueError('暂存已失效')
    with stage['lock']:
        stage['sealed'] = True
        items = [entry.name for entry in stage['root'].iterdir()]
        validate_items(items)
        return {'path': str(stage['root']), 'items': items}


def stage_release(identifier):
    stage = STAGES.get(identifier)
    if stage:
        with stage['lock']:
            if stage['busy']:
                raise ValueError('暂存正在传输，不能清除')
            STAGES.pop(identifier, None)
            shutil.rmtree(stage['root'])


def parse_progress(text):
    match = re.fullmatch(r'\s*([\d,]+)\s+(\d{1,3})%\s+([\d.,]+)\s*([kKMGT]?)B/s\s+(\S+)(?:\s+.*)?', text)
    if not match:
        return None
    unit = match[4].upper()
    scale = 1024 ** ({'': 0, 'K': 1, 'M': 2, 'G': 3, 'T': 4}[unit])
    return dict(bytes=int(match[1].replace(',', '')), progress=min(100, int(match[2])),
                speed=float(match[3].replace(',', '')) * scale, eta=match[5])


class Job:
    def __init__(self, options):
        self.id = secrets.token_hex(6)
        self.options = options
        self.transfer_key = transfer_history.transfer_key(options)
        self.cancel = threading.Event()
        self.process = None
        self.state = 'queued'
        self.route = ''
        self.speed = 0
        self.log = collections.deque(maxlen=120)
        self.error = ''
        self.samples = collections.deque()
        self.last_switch = 0
        self.switches = 0
        self.started = time.time()
        self.last_progress = time.monotonic()
        self.manifest = None
        self.progress = 0
        self.transferred_bytes = 0
        self.eta = ''
        self.result = None
        self.queue_reason = ''
        self.byte_samples = collections.deque()
        self.byte_sample = None

    def event(self, message):
        with LOCK:
            self.log.append(time.strftime('%H:%M:%S') + ' ' + message)

    def snapshot(self):
        with LOCK:
            direction = self.options['direction']
            target = None
            if direction in ('upload', 'download', 'copy', 'relay', 'remote'):
                remote = direction in ('upload', 'relay', 'remote')
                endpoint = self.options.get('destination', {}) if direction in ('relay', 'remote') else {}
                path = endpoint.get('path', '') if endpoint else self.options.get('remote' if remote else 'destination' if direction == 'copy' else 'local', '')
                routes = endpoint.get('routes', []) if endpoint else self.options.get('routes', [])
                target = dict(kind='remote' if remote else 'local', path=path)
                if remote and routes:
                    target['route'] = routes[0]
                target['paths'] = [posixpath.join(path, posixpath.basename(item) if self.options.get('flattenItems') else item) for item in self.options.get('items') or []]
            return dict(id=self.id, state=self.state, route=self.route, speed=self.speed,
                        log=list(self.log), error=self.error, switches=self.switches,
                        direction=self.options['direction'], side=self.options.get('side'), started=self.started, progress=self.progress, result=self.result,
                        items=self.options.get('items', []), local=self.options.get('local', ''), remote=self.options.get('remote', ''),
                        sourcePane=self.options.get('sourcePane'), targetPane=self.options.get('targetPane'),
                        stageProgress=self.progress, transferredBytes=self.transferred_bytes, eta=self.eta,
                        target=target,
                        queueReason=self.queue_reason,
                        resumable=transfer_history.resumable(self), legacyResume=bool(self.options.get('_legacy')), resumedFrom=self.options.get('_resumedFrom'))

    def command(self, binary, route, canonical, local):
        remote = route['host'] + ':' + canonical.rstrip('/') + '/' if route else ''
        local = local.rstrip('/') + '/'
        source, destination = (local, remote) if self.options['direction'] == 'upload' else (remote, local)
        transport = ['-e', shlex.join(ssh_args(route))] if route else []
        if self.options['direction'] == 'copy':
            source, destination = local, canonical.rstrip('/') + '/'
        # No delete/inplace/append: partial data is a basis for a checked delta transfer.
        selection = ['-r', '--from0', '--files-from=' + self.manifest] if self.manifest else []
        if self.manifest and self.options.get('flattenItems'):
            selection.append('--no-relative')
        return [binary, '-a', '-s', *selection, '--no-owner', '--no-group', '--partial-dir=.termiusplus-partial',
                '--no-whole-file', '--info=progress2', '--no-inc-recursive', '--outbuf=L', '--timeout=30',
                *transport, '--', source, destination]

    def record_output(self, text):
        text = text.strip()
        if not text:
            return
        parsed = parse_progress(text)
        if parsed:
            now = time.monotonic()
            with LOCK:
                self.progress = parsed['progress']
                self.transferred_bytes = parsed['bytes']
                self.speed = parsed['speed']
                self.eta = parsed['eta']
                self.last_progress = now
                self.samples.append((now, self.speed))
                while self.samples and now - self.samples[0][0] > 25:
                    self.samples.popleft()
                previous = self.byte_sample
                if previous is None or parsed['bytes'] < previous[1]:
                    self.byte_sample = (now, parsed['bytes'])
                elif now - previous[0] >= .5:
                    self.byte_samples.append((now, (parsed['bytes'] - previous[1]) / (now - previous[0])))
                    self.byte_sample = (now, parsed['bytes'])
                while self.byte_samples and now - self.byte_samples[0][0] > 25:
                    self.byte_samples.popleft()
        self.event(text)

    def read_output(self, process):
        try:
            buffer = b''
            while True:
                chunk = os.read(process.stdout.fileno(), 4096)
                if not chunk:
                    break
                buffer += chunk
                lines = re.split(rb'[\r\n]', buffer)
                buffer = lines.pop()
                for line in lines:
                    self.record_output(line.decode(errors='replace'))
            self.record_output(buffer.decode(errors='replace'))
        finally:
            process.stdout.close()

    def pack_remote(self, route, root, items):
        name = archive_name(validate_items(items))
        command = 'python3 -c ' + shlex.quote(REMOTE_ARCHIVE_SCRIPT) + ' ' + shlex.quote(root) + ' ' + shlex.quote(json.dumps(items)) + ' ' + shlex.quote(name)
        self.process = subprocess.Popen(ssh_args(route) + [route['host'], command], stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True, env=ssh_env(route))
        output, error = self.process.communicate()
        if self.process.returncode:
            raise ValueError(error.decode(errors='replace')[-1500:] or '远程打包失败')
        return json.loads(output)['path']

    def pack(self, route=None, root=None):
        items = validate_items(self.options.get('items'))
        self.state = 'packing'
        self.event('开始打包为 tar.gz，原始文件保留')
        if self.options.get('side', 'local') == 'remote' or self.options['direction'] == 'download':
            archive = self.pack_remote(route, root, items)
        else:
            archive = local_archive(root or self.options['local'], items, self.cancel, self.event)
        self.result = {'archive': archive}
        if self.options['direction'] != 'pack':
            self.options['pack'] = False
            self.options['items'] = [posixpath.basename(archive)]
            checkpoint()
        self.event('已生成 ' + archive)
        return posixpath.basename(archive)

    def run_copy(self):
        binary = rsync_binary()
        source = Path(self.options['local']).expanduser().resolve(strict=True)
        target = Path(self.options['destination']).expanduser().resolve(strict=True)
        if not source.is_dir() or not target.is_dir():
            raise ValueError('源和目标必须是目录')
        if source == target:
            raise ValueError('源和目标是同一个目录')
        items = self.options.get('items')
        roots = [source / item for item in validate_items(items)] if items else [source]
        if any(item.is_dir() and not item.is_symlink() and target.is_relative_to(item.resolve()) for item in roots):
            raise ValueError('不能把文件夹复制到它自己内部')
        if self.options.get('pack'):
            self.options['items'] = [self.pack(root=str(source))]
        if self.options.get('items'):
            self.manifest = create_manifest(self.options['items'], self.options.get('flattenItems', False))
        if self.cancel.is_set():
            return
        self.state = 'transferring'
        self.route = '本地 → 本地'
        self.process = subprocess.Popen(self.command(binary, None, str(target), str(source)), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, start_new_session=True, env=dict(os.environ, LC_ALL='C'))
        reader = threading.Thread(target=self.read_output, args=(self.process,), daemon=True)
        reader.start()
        code = self.process.wait()
        reader.join(timeout=3)
        if self.cancel.is_set():
            return
        if code:
            raise ValueError('rsync 退出码 %s，详情见日志' % code)
        self.progress = 100
        self.state = 'completed'
        self.event('本地复制完成')

    def run(self):
        try:
            if self.options['direction'] == 'copy':
                self.run_copy()
                return
            if self.options['direction'] == 'pack':
                if self.options.get('side') == 'remote':
                    route = validate_route(self.options['routes'][0])
                    root = remote_info(route, self.options['remote'], True)['path']
                    self.pack(route, root)
                else:
                    self.pack(root=self.options['local'])
                if not self.cancel.is_set():
                    self.progress = 100
                    self.state = 'completed'
                return
            binary = rsync_binary()
            routes = [validate_route(r) for r in self.options['routes']]
            if not routes:
                raise ValueError('请添加至少一条线路')
            local = str(Path(self.options['local']).expanduser().resolve(strict=True))
            if not Path(local).is_dir():
                raise ValueError('本地路径必须是目录')
            path = self.options['remote']
            baseline = None
            canonical = None
            eligible = []
            self.state = 'checking'
            # Anchor identity to the chosen server before considering alternatives.
            primary_info = None
            for attempt in range(4):
                if self.cancel.is_set():
                    return
                try:
                    primary_info = remote_info(routes[0], path, True)
                    break
                except Exception as exc:
                    self.event(routes[0]['name'] + ': ' + str(exc))
                    if attempt == 3:
                        raise ValueError('所选服务器无法连接，无法核对备用线路身份；已重试 3 次')
                    self.state = 'retrying'
                    self.event('重新连接所选服务器 %d/3' % (attempt + 1))
                    self.cancel.wait(2)
            baseline, canonical = primary_info['identity'], primary_info['path']
            self.state = 'checking'
            for route in routes:
                if self.cancel.is_set():
                    return
                try:
                    info = primary_info if route == routes[0] else remote_info(route, path, True)
                    if info['identity'] != baseline:
                        self.event(route['name'] + ' 指向不同服务器或目录，已排除')
                        continue
                    if 'rsyncVersion' in info and not re.search(r'rsync\s+version\s+[3-9]\.', info['rsyncVersion']):
                        raise ValueError('远程需要 rsync 3.x，请安装并确保 SSH 登录的 PATH 能找到它')
                    speed = 0
                    if len(routes) > 1 and self.options.get('auto', True):
                        try:
                            speed = measure(route, self.cancel, self.options['direction'])
                        except Exception as exc:
                            self.event(route['name'] + ' 测速失败，保留线路并尝试实际传输: ' + str(exc))
                    if self.cancel.is_set():
                        return
                    eligible.append((speed, route))
                    self.event(route['name'] + ((' 上传' if self.options['direction'] == 'upload' else ' 下载') + '测速 %.2f MiB/s' % (speed / 1048576) if speed else ' 已连接'))
                except Exception as exc:
                    self.event(route['name'] + ': ' + str(exc))
            if not eligible:
                raise ValueError('没有可用且指向同一目录的线路，请检查 SSH 和远程 python3')
            eligible.sort(key=lambda item: item[0], reverse=True)
            current = eligible[0][1]
            expected = eligible[0][0]
            check_interval = 60
            if self.options.get('pack'):
                item = self.pack(current, canonical if self.options['direction'] == 'download' else local)
                self.options['items'] = [item]
            if self.options.get('items'):
                self.manifest = create_manifest(self.options['items'], self.options.get('flattenItems', False))
            self.event('已核对 %d 条可用线路；%s' % (len(eligible), '没有同一服务器的备用线路，连接故障时将重试当前线路' if len(eligible) == 1 else '连接故障时自动尝试同一服务器的备用线路'))
            retries = 0
            while not self.cancel.is_set():
                self.route = current['name']
                self.state = 'transferring'
                self.samples.clear()
                self.byte_samples.clear()
                self.byte_sample = None
                self.speed = 0
                self.progress = 0
                self.transferred_bytes = 0
                self.eta = ''
                self.last_progress = time.monotonic()
                self.event('使用 ' + self.route + ' 开始/续传')
                self.process = subprocess.Popen(self.command(binary, current, canonical, local), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, start_new_session=True, env=ssh_env(current, {'LC_ALL': 'C'}) or dict(os.environ, LC_ALL='C'))
                reader = threading.Thread(target=self.read_output, args=(self.process,), daemon=True)
                reader.start()
                replacement = None
                while self.process.poll() is None and not self.cancel.wait(1):
                    now = time.monotonic()
                    with LOCK:
                        samples = list(self.byte_samples)
                    # Require sustained progress samples. Silence may mean a file-list scan,
                    # disk work or completion, so silence alone must never trigger a switch.
                    if self.options.get('auto', True) and len(eligible) > 1 and needs_route_check(samples, now, float(self.options.get('threshold', 1)) * 1048576, expected, self.last_switch, self.switches, check_interval):
                        self.last_switch = now
                        self.event('实际速度持续下降，暂停本次连接后按传输方向比较线路')
                        scores = []
                        stop_process(self.process)
                        try:
                            stopped_code = self.process.wait(timeout=8)
                        except subprocess.TimeoutExpired:
                            os.killpg(self.process.pid, signal.SIGKILL)
                            stopped_code = self.process.wait()
                        reader.join(timeout=3)
                        if stopped_code == 0 or self.cancel.is_set():
                            break
                        # Stop cleanly first: parallel probes would compete with this data stream.
                        # A stopped rsync connection also cannot expire its remote idle timeout.
                        replacement = current
                        self.state = 'checking'
                        for _, candidate in eligible:
                            try:
                                if remote_info(candidate, path, True)['identity'] != baseline:
                                    continue
                                score = measure(candidate, self.cancel, self.options['direction'])
                                scores.append((score, candidate))
                                self.event(candidate['name'] + ' 复测 %.2f MiB/s' % (score / 1048576))
                            except Exception as exc:
                                self.event('测速失败: ' + str(exc))
                            if self.cancel.is_set(): break
                        current_score = next((s for s, r in scores if r == current), None)
                        alternatives = [(s, r) for s, r in scores if r != current]
                        if current_score is not None and alternatives:
                            expected = current_score
                            best_score, best = max(alternatives, key=lambda item: item[0])
                            if should_switch(current_score, best_score):
                                replacement = best
                                expected = best_score
                                self.event('备用线路明显更快，通过备用线路续传')
                        if replacement == current:
                            expected = statistics.median(s[1] for s in samples) if samples else expected
                            check_interval = min(check_interval * 2, 600)
                            self.event('备用线路无持续优势，保留当前线路续传')
                        else:
                            check_interval = 60
                        self.last_switch = time.monotonic()
                        break
                if self.cancel.is_set():
                    stop_process(self.process)
                try:
                    code = self.process.wait(timeout=8)
                except subprocess.TimeoutExpired:
                    os.killpg(self.process.pid, signal.SIGKILL)
                    code = self.process.wait()
                reader.join(timeout=3)
                if self.cancel.is_set():
                    return
                if replacement:
                    if replacement != current:
                        self.switches += 1
                    current = replacement
                    continue
                if code == 0:
                    self.state = 'completed'
                    self.progress = 100
                    self.event('传输完成')
                    return
                # Retry only transport errors; permission, missing files and disk errors fail.
                if code in (10, 12, 30, 35, 255):
                    recovered = False
                    while retries < 3 and not self.cancel.is_set():
                        retries += 1
                        self.state = 'retrying'
                        self.event('连接失败，重试 %d/3' % retries)
                        self.cancel.wait(2)
                        candidates = [r for _, r in eligible if r != current] if self.options.get('auto', True) else []
                        candidates.append(current)
                        for candidate in candidates:
                            if self.cancel.is_set():
                                return
                            try:
                                if remote_info(candidate, path, True)['identity'] != baseline:
                                    self.event(candidate['name'] + ' 目录身份发生变化，已跳过')
                                    continue
                                if candidate != current:
                                    self.switches += 1
                                    self.event('故障切换到 ' + candidate['name'])
                                current = candidate
                                check_interval = 60
                                expected = next((score for score, route in eligible if route == candidate), 0)
                                recovered = True
                                break
                            except Exception as exc:
                                self.event(candidate['name'] + ' 暂不可用，继续尝试其他线路: ' + str(exc))
                        if recovered:
                            break
                    if self.cancel.is_set():
                        return
                    if recovered:
                        continue
                    raise ValueError('连接故障且重试 3 次后仍无可用线路，详情见日志')
                raise ValueError('rsync 退出码 %s，详情见传输日志' % code)
        except Exception as exc:
            self.error = str(exc)
            self.event(self.error)
            self.state = 'failed'
        finally:
            if self.manifest:
                Path(self.manifest).unlink(missing_ok=True)
            if self.options.get('stage') in STAGES:
                STAGES[self.options['stage']]['busy'] = False
                if self.state == 'completed':
                    stage_release(self.options['stage'])
            if self.cancel.is_set():
                stop_process(self.process)
                self.state = 'cancelled'


class RelayJob(Job):
    def __init__(self, options):
        self.child = None
        self.phase = 0
        self.completed_switches = 0
        super().__init__(options)

    @property
    def process(self):
        return self.child.process if self.child else getattr(self, '_process', None)

    @process.setter
    def process(self, value):
        self._process = value

    def snapshot(self):
        result = super().snapshot()
        if not self.child and hasattr(self, 'restored_snapshot'):
            saved = self.restored_snapshot
            result.update({k: saved[k] for k in ('stage', 'stageIndex', 'stageCount', 'stageProgress', 'transferredBytes', 'eta') if k in saved})
            if 'stageProgress' not in saved:
                result.pop('stageProgress', None)
        if self.child:
            child = self.child.snapshot()
            terminal = self.state in ('completed', 'failed', 'cancelled', 'interrupted')
            result.update(stage='download' if not self.phase else 'upload', stageIndex=self.phase+1,
                          stageCount=2, stageProgress=child['progress'], speed=child['speed'],
                          transferredBytes=child['transferredBytes'], eta=child['eta'],
                          switches=self.completed_switches + child['switches'])
            result['progress'] = 100 if self.state == 'completed' else self.phase*50+child['progress']/2
            if not terminal:
                result.update(state='checking' if child['state']=='completed' else child['state'],
                              route=('下载 · ' if not self.phase else '上传 · ') + child['route'])
        return result

    def run(self):
        directory = Path(self.options.get('_relayDirectory') or tempfile.mkdtemp(prefix='relay-', dir=STAGE_ROOT.name))
        directory.mkdir(parents=True, exist_ok=True)
        self.options['_relayDirectory'] = str(directory)
        checkpoint()
        try:
            source, destination = self.options['source'], self.options['destination']
            self.route = source['routes'][0].get('name', source['routes'][0]['host']) + ' → ' + destination['routes'][0].get('name', destination['routes'][0]['host'])
            common = dict(auto=self.options.get('auto', True), threshold=self.options.get('threshold', 1))
            download = dict(common, direction='download', routes=source['routes'], local=str(directory), remote=source['path'], pack=self.options.get('pack', False), flattenItems=self.options.get('flattenItems', False))
            if self.options.get('items'):
                download['items'] = self.options['items']
            download = self.options.get('_relayDownloadOptions', download)
            self.options['_relayDownloadOptions'] = download
            checkpoint()
            self.child = Job(download)
            self.child.cancel = self.cancel
            self.child.event = lambda message: self.event('下载: ' + message)
            self.state = 'transferring'
            self.event('远程到远程：先下载到本机临时目录，再上传到目标')
            self.child.run()
            if download.get('pack') is False and self.options.get('pack'):
                self.options['pack'] = False
                self.options['items'] = download.get('items', [])
            checkpoint()
            if self.cancel.is_set():
                return
            if self.child.state != 'completed':
                raise ValueError('下载阶段失败: ' + self.child.error)
            self.completed_switches = self.child.switches
            self.phase = 1
            upload = dict(common, direction='upload', routes=destination['routes'], local=str(directory), remote=destination['path'])
            if download.get('items'):
                upload['items'] = [posixpath.basename(item) for item in download['items']] if download.get('flattenItems') else download['items']
                self.options['items'] = download['items']
            self.child = Job(upload)
            self.child.cancel = self.cancel
            self.child.event = lambda message: self.event('上传: ' + message)
            self.child.run()
            if self.cancel.is_set():
                return
            if self.child.state != 'completed':
                raise ValueError('上传阶段失败: ' + self.child.error)
            self.switches = self.completed_switches + self.child.switches
            self.state = 'completed'
            self.progress = 100
            self.event('远程到远程传输完成')
            shutil.rmtree(directory)
        except Exception as exc:
            self.error = str(exc)
            self.state = 'failed'
            self.event(self.error)
            self.event('中转副本已保留，续传将复用: ' + str(directory))
        finally:
            if self.cancel.is_set():
                stop_process(self.process)
                self.state = 'cancelled'


def checkpoint():
    transfer_history.save(HISTORY_PATH, JOBS, LOCK)


def new_transfer(options, previous=None):
    import copy
    options = (transfer_history.resumed_options(previous) if previous and not previous.options.get('_legacy') else copy.deepcopy(options))
    if previous and previous.options.get('_legacy'):
        options.pop('_legacy', None)
        if previous.options.get('_relayDirectory'):
            options['_relayDirectory'] = previous.options['_relayDirectory']
    if previous:
        options['_resumedFrom'] = previous.id
    stage_id = options.get('stage')
    if stage_id:
        root = Path(options['local'])
        if not root.is_dir():
            raise ValueError('拖入文件的暂存已不存在，请重新拖入')
        if stage_id not in STAGES:
            STAGES[stage_id] = dict(root=root, files=set(), sealed=True, busy=False, lock=threading.Lock())
        STAGES[stage_id]['busy'] = True
    job = RemoteJob(options) if options['direction']=='remote' else RelayJob(options) if options['direction'] == 'relay' else Job(options)
    if previous and not previous.options.get('_legacy'):
        job.transfer_key = previous.transfer_key
    if previous:
        job.event('续传任务 ' + previous.id + '，复用已完成文件和未完成数据，校验后继续')
    return job


RESOURCE_SCRIPT = r'''
import os,sys,json,platform,hashlib
p=json.load(sys.stdin)
try: machine=open('/etc/machine-id').read().strip()
except OSError: machine=platform.node()
root=os.path.abspath(os.path.expanduser(p['path']))
paths=[]
for item in p['items']:
 candidate=os.path.normpath(os.path.join(root,item));paths.extend([candidate,os.path.realpath(candidate)])
print(json.dumps(dict(machine=hashlib.sha256(machine.encode()).hexdigest(),paths=paths)))
'''


def remote_resources(endpoint, items):
    route = endpoint['routes'][0]
    command = 'python3 -c ' + shlex.quote(RESOURCE_SCRIPT)
    result = subprocess.run(ssh_args(route) + [route['host'], command],
                            input=json.dumps(dict(path=endpoint['path'], items=items)).encode(),
                            capture_output=True, timeout=20, env=ssh_env(route))
    if result.returncode:
        raise ValueError('无法核对远程读写路径')
    data = json.loads(result.stdout)
    if not re.fullmatch('[0-9a-f]{64}', data['machine']) or len(data['paths']) != 2 * len(items) or any(not isinstance(p, str) or not p.startswith('/') for p in data['paths']):
        raise ValueError('远程读写路径不正确')
    return data['machine'], data['paths']


SCHEDULER = scheduler.TransferScheduler(lambda options: scheduler.resources(options, remote_resources), checkpoint, LOCK)


def launch(job, attached=False):
    return SCHEDULER.submit(job, attached)


def validate_endpoint(endpoint):
    if not isinstance(endpoint, dict) or not isinstance(endpoint.get('path'), str) or not endpoint['path']:
        raise ValueError('传输位置不正确')
    if endpoint.get('kind') != 'remote':
        raise ValueError('远程中转需要两端都是远程连接')
    if not isinstance(endpoint.get('routes'), list) or not 1 <= len(endpoint['routes']) <= 8:
        raise ValueError('每端需要 1–8 条线路')
    return dict(kind='remote', path=endpoint['path'], routes=[validate_route(route) for route in endpoint['routes']])


RemoteJob = remote_tasks.make_job_class(Job, remote_info, ssh_args, checkpoint, PACKAGE, REMOTE_SCRIPT, REMOTE_ARCHIVE_SCRIPT, archive_name)

class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def send_json(self, data, status=200):
        body = json.dumps(data, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.headers.get('Host') != '127.0.0.1:%s' % self.server.server_port:
            self.send_json({'error': 'Invalid host'}, 403)
            return
        assets = {'/': ('web/index.html', 'text/html; charset=utf-8'),
                  '/terminal': ('web/terminal.html', 'text/html; charset=utf-8'),
                  '/static/vendor/xterm/xterm.js': ('static/vendor/xterm/xterm.js', 'text/javascript'),
                  '/static/vendor/xterm/xterm.css': ('static/vendor/xterm/xterm.css', 'text/css'),
                  '/static/vendor/addon-fit/addon-fit.js': ('static/vendor/addon-fit/addon-fit.js', 'text/javascript')}
        for folder, suffix, mime in [('scripts', '.js', 'text/javascript'), ('styles', '.css', 'text/css')]:
            for name in ('files', 'terminal'):
                assets['/static/'+folder+'/'+name+suffix] = ('web/'+folder+'/'+name+suffix, mime)
        asset = assets.get(self.path)
        if not asset:
            self.send_json({'error': 'Not found'}, 404)
            return
        body = (BASE / asset[0]).read_bytes()
        self.send_response(200)
        self.send_header('Content-Type', asset[1])
        self.send_header('Content-Length', str(len(body)))
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; connect-src 'self'; img-src 'self' data:; frame-ancestors 'none'; base-uri 'none'")
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        if self.headers.get('Host') != '127.0.0.1:%s' % self.server.server_port or self.headers.get('Authorization') != 'Bearer ' + TOKEN:
            self.send_json({'error': '请使用启动时显示的完整链接打开'}, 403)
            return
        origin = self.headers.get('Origin')
        if origin and origin != 'http://127.0.0.1:%s' % self.server.server_port:
            self.send_json({'error': 'Invalid origin'}, 403)
            return
        try:
            length = int(self.headers.get('Content-Length', 0))
            endpoint = urllib.parse.urlsplit(self.path)
            if endpoint.path == '/api/stage/chunk':
                if length < 0 or length > 4 * 1024 * 1024:
                    raise ValueError('暂存分块过大')
                query = urllib.parse.parse_qs(endpoint.query)
                content = self.rfile.read(length)
                if len(content) != length:
                    raise ValueError('暂存分块不完整')
                stage_write(query['id'][0], query['path'][0], int(query.get('offset', ['0'])[0]), content, query.get('directory', ['0'])[0] == '1')
                self.send_json({'ok': True})
                return
            if length < 0 or length > 65536:
                raise ValueError('请求过大')
            data = json.loads(self.rfile.read(length) or b'{}')
            if self.path.startswith('/api/terminal/'):
                result = terminal_sessions.terminal_api(self.path[len('/api/terminal/'):], data, validate_route, ssh_args)
            elif self.path == '/api/stage/create':
                result = {'id': stage_create()}
            elif self.path == '/api/stage/seal':
                result = stage_seal(data['id'])
            elif self.path == '/api/stage/release':
                stage_release(data['id'])
                result = {'ok': True}
            elif self.path == '/api/status':
                try:
                    binary = rsync_binary()
                    dependency = dict(ok=True, path=binary)
                except ValueError as exc:
                    dependency = dict(ok=False, error=str(exc))
                checkpoint()
                result = dict(terminal=True, remoteTasks=True, remoteLinks=remote_links(), rsync=dependency, home=str(Path.home()), jobs=[j.snapshot() for j in list(JOBS.values())])
            elif self.path == '/api/local':
                result = local_info(data['path'])
            elif self.path == '/api/remote':
                route = validate_route(data['route'])
                result = remote_info(route, data['path'])
            elif self.path == '/api/preview':
                if not isinstance(data.get('path'), str) or not data['path']:
                    raise ValueError('请选择要预览的文件')
                if data.get('side') == 'local':
                    result = file_preview(data['path'])
                elif data.get('side') == 'remote':
                    result = remote_preview(validate_route(data['route']), data['path'])
                else:
                    raise ValueError('预览位置不正确')
            elif self.path == '/api/files':
                result = file_action(data)
            elif self.path == '/api/probe':
                direction = data.get('direction', 'download')
                result = {'speed': measure(validate_route(data['route']), direction=direction), 'direction': direction}
            elif self.path in ('/api/start', '/api/pack'):
                legacy_resume_id = data.pop('_resumeId', None)
                data = {k: v for k, v in data.items() if not k.startswith('_')}
                if self.path == '/api/pack':
                    data['direction'] = 'pack'
                if data.get('direction') not in ('upload', 'download', 'pack', 'copy', 'relay', 'remote'):
                    raise ValueError('传输方向不正确')
                if data['direction'] in ('upload', 'download') or (data['direction'] == 'pack' and data.get('side') == 'remote'):
                    if not isinstance(data.get('routes'), list) or not 1 <= len(data['routes']) <= 8:
                        raise ValueError('需要 1–8 条线路')
                if not isinstance(data.get('routes', []), list):
                    raise ValueError('需要 1–8 条线路')
                data['routes'] = [validate_route(r) for r in data.get('routes', [])]
                if data['direction'] in ('relay','remote'):
                    data['source'] = validate_endpoint(data.get('source'))
                    data['destination'] = validate_endpoint(data.get('destination'))
                if data['direction']=='remote':
                    if REMOTE_START_CHECK: REMOTE_START_CHECK()
                    data['remoteTarget']=validate_route(data.get('remoteTarget'))
                    if data.get('executor') not in ('source','destination'):
                        raise ValueError('请选择远程执行端')
                if 'flattenItems' in data and not isinstance(data['flattenItems'], bool):
                    raise ValueError('传输路径选项不正确')
                if data.get('items') is not None:
                    data['items'] = validate_items(data['items'], data.get('flattenItems', False) and not data.get('pack'))
                if data.get('pack') or data['direction'] == 'pack':
                    validate_items(data.get('items'))
                if data.get('stage'):
                    stage = STAGES.get(data['stage'])
                    if not stage or not stage['sealed'] or data['direction'] not in ('upload', 'copy'):
                        raise ValueError('拖放暂存尚未完成或已失效')
                    data['local'] = str(stage['root'])
                threshold = float(data.get('threshold', 1))
                if not .01 <= threshold <= 10000:
                    raise ValueError('低速阈值不正确')
                if data['direction'] not in ('pack','remote'):
                    rsync_binary()
                with LOCK:
                    match = next((j for j in reversed(list(JOBS.values())) if transfer_history.resumable(j) and j.transfer_key == transfer_history.transfer_key(data)), None)
                    if legacy_resume_id:
                        match = JOBS.get(legacy_resume_id)
                        if not match or not transfer_history.resumable(match) or not match.options.get('_legacy') or data['direction'] != match.options['direction'] or data.get('items', []) != match.options.get('items', []):
                            raise ValueError('旧记录的续传参数不正确')
                    job = new_transfer(data, match)
                    JOBS[job.id] = job
                    if data.get('stage'):
                        stage['busy'] = True
                    launch(job)
                result = {'id': job.id}
            elif self.path == '/api/resume':
                with LOCK:
                    previous = JOBS.get(data.get('id'))
                    if not previous or not transfer_history.resumable(previous) or previous.options.get('_legacy'):
                        raise ValueError('此任务不能续传')
                    job = new_transfer(previous.options, previous)
                    JOBS[job.id] = job
                    launch(job)
                result = {'id': job.id}
            elif self.path == '/api/delete-record':
                with LOCK:
                    identifier = data.get('id')
                    job = JOBS.get(identifier)
                    if not job:
                        raise ValueError('此记录已不存在')
                    if job.state not in transfer_history.TERMINAL:
                        raise ValueError('任务仍在运行，请先取消或等待完成')
                    transfer_history.forget(HISTORY_PATH.parent / 'remote-controller' / 'transfers.json', identifier)
                    del JOBS[identifier]
                    try:
                        checkpoint()
                    except Exception:
                        JOBS[identifier] = job
                        raise
                result = {'ok': True}
            elif self.path == '/api/cancel':
                job = JOBS[data['id']]
                if isinstance(job,RemoteJob) and job.options.get('_remoteId'):
                    job.request_cancel()
                else:
                    SCHEDULER.cancel(job)
                    if job.options.get('stage') in STAGES:
                        STAGES[job.options['stage']]['busy'] = False
                    stop_process(job.process)
                result = {'ok': True}
            else:
                self.send_json({'error': 'Not found'}, 404)
                return
            self.send_json(result)
        except Exception as exc:
            self.send_json({'error': str(exc)}, 400)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=8765)
    args = parser.parse_args()
    for sig in (signal.SIGHUP, signal.SIGTERM):
        signal.signal(sig, lambda signum, frame: (_ for _ in ()).throw(KeyboardInterrupt()))
    JOBS.update(transfer_history.restore(HISTORY_PATH, Job, RelayJob, RemoteJob))
    if STATE_ROOT == BASE / '.termiusplus-state':
        secondary = STATE_ROOT / 'remote-controller' / 'transfers.json'
        for identifier, job in transfer_history.restore(secondary, Job, RelayJob, RemoteJob).items():
            if identifier not in JOBS: JOBS[identifier] = job
    for job in list(JOBS.values()):
        if isinstance(job,RemoteJob) and job.options.get('_remoteId') and job.state not in transfer_history.TERMINAL:
            launch(job, attached=True)
    for job in list(JOBS.values()):
        if job.state == 'queued' and not job.options.get('_remoteId'):
            launch(job)
    server = ThreadingHTTPServer(('127.0.0.1', args.port), Handler)
    print('TermiusPlus 已启动，请在浏览器打开：\nhttp://127.0.0.1:%s/#%s' % (server.server_port, TOKEN), flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        SCHEDULER.close()
        for job in list(JOBS.values()):
            if isinstance(job,RemoteJob):
                job.monitor_stop.set()
                continue
            if job.state not in transfer_history.TERMINAL and job.state != 'queued':
                job.cancel.set()
                stop_process(job.process)
        checkpoint()
        terminal_sessions.close_all()
        server.server_close()


if __name__ == '__main__':
    main()
