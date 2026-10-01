"""Bounded interactive local and SSH shell sessions backed by a local PTY."""
import base64
import collections
import errno
import fcntl
import os
import pty
import secrets
import select
import signal
import struct
import subprocess
import sys
import termios
import threading
import time
from pathlib import Path

SESSIONS = {}
SESSIONS_LOCK = threading.RLock()
OUTPUT_LIMIT = 1024 * 1024
WORKER = Path(__file__).with_name('terminal_worker.py')


def dimensions(cols, rows):
    if type(cols) is not int or type(rows) is not int or not 2 <= cols <= 500 or not 2 <= rows <= 250:
        raise ValueError('终端大小不正确')
    return cols, rows


def local_command(path):
    """A login shell on this Mac, started in the selected local directory."""
    if not isinstance(path, str) or '\0' in path or len(path) > 8192:
        raise ValueError('终端目录不正确')
    try:
        directory = (Path(path).expanduser() if path else Path.home()).resolve(strict=True)
    except OSError:
        raise ValueError('本地终端目录不存在，请先在文件栏打开一个目录')
    if not directory.is_dir():
        raise ValueError('本地终端目录不是文件夹，请先在文件栏打开一个目录')
    shell = os.environ.get('SHELL', '')
    if not os.path.isabs(shell) or not os.access(shell, os.X_OK):
        shell = '/bin/sh'
    return [shell, '-l'], str(directory)


def ssh_command(route, path, ssh_args):
    import shlex
    if not isinstance(path, str) or '\0' in path or len(path) > 8192:
        raise ValueError('终端目录不正确')
    args = ssh_args(route) + ['-tt', route['host']]
    if path and path != '~':
        # Start a login shell in the selected directory; quote the path as data.
        if path.startswith('~/'):
            directory = '"$HOME"/' + shlex.quote(path[2:])
        else:
            directory = shlex.quote(path)
        args.append('cd -- ' + directory + ' || exit; exec "${SHELL:-/bin/sh}" -l')
    return args


class TerminalSession:
    def __init__(self, command, name, cols=80, rows=24, cwd=None, local=False, ssh_environment=None):
        dimensions(cols, rows)
        self.id = secrets.token_hex(16)
        self.name = name
        self.local = bool(local)
        self.condition = threading.Condition()
        self.write_lock = threading.Lock()
        self.chunks = collections.deque()
        self.offset = 0
        self.closed = False
        self.done = False
        self.last_seen = time.monotonic()
        master, slave = pty.openpty()
        self.master = master
        try:
            fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack('HHHH', rows, cols, 0, 0))
            self.process = subprocess.Popen([sys.executable, str(WORKER), *command], stdin=slave, stdout=slave, stderr=slave,
                                            start_new_session=True, close_fds=True, cwd=cwd, env=dict(ssh_environment or os.environ, TERM='xterm-256color'))
        except BaseException:
            os.close(master)
            raise
        finally:
            os.close(slave)
        self.reader = threading.Thread(target=self._read, daemon=True)
        self.reader.start()

    def _append(self, block):
        with self.condition:
            self.chunks.append((self.offset, block))
            self.offset += len(block)
            while self.chunks and self.offset - self.chunks[0][0] > OUTPUT_LIMIT:
                self.chunks.popleft()
            self.condition.notify_all()

    def _read(self):
        try:
            while not self.closed:
                ready, _, _ = select.select([self.master], [], [], .25)
                if not ready:
                    if self.process.poll() is not None:
                        break
                    continue
                try:
                    block = os.read(self.master, 16384)
                except OSError as exc:
                    if exc.errno in (errno.EIO, errno.EBADF):
                        break
                    raise
                if not block:
                    break
                self._append(block)
        finally:
            self.process.wait()
            with self.condition:
                self.done = True
                self.condition.notify_all()
            with self.write_lock:
                if self.master is not None:
                    os.close(self.master)
                    self.master = None

    def poll(self, cursor, wait=1):
        self.last_seen = time.monotonic()
        with self.condition:
            if type(cursor) is not int or not 0 <= cursor <= self.offset:
                raise ValueError('终端输出位置不正确')
            if cursor == self.offset and not self.done:
                self.condition.wait(timeout=wait)
            earliest = self.chunks[0][0] if self.chunks else self.offset
            start = max(cursor, earliest)
            pieces = []
            remaining = 65536
            for offset, block in self.chunks:
                if offset + len(block) <= start:
                    continue
                piece = block[max(0, start-offset):][:remaining]
                pieces.append(piece)
                remaining -= len(piece)
                if not remaining:
                    break
            data = b''.join(pieces)
            return dict(id=self.id, content=base64.b64encode(data).decode('ascii'), cursor=start+len(data),
                        truncated=cursor < earliest, done=self.done and start+len(data) >= self.offset,
                        exitCode=self.process.poll(), closed=self.closed, name=self.name, local=self.local)

    def write(self, content):
        if not isinstance(content, bytes) or len(content) > 16384:
            raise ValueError('终端输入过长')
        self.last_seen = time.monotonic()
        with self.write_lock:
            if self.closed or self.done or self.master is None:
                raise ValueError('终端已断开，请重新连接')
            view = memoryview(content)
            deadline = time.monotonic()+5
            while view:
                if time.monotonic() > deadline:
                    raise ValueError('终端输入超时，请检查连接')
                _, ready, _ = select.select([], [self.master], [], .25)
                if ready:
                    count = os.write(self.master, view[:4096])
                    view = view[count:]

    def resize(self, cols, rows):
        dimensions(cols, rows)
        self.last_seen = time.monotonic()
        with self.write_lock:
            if self.closed or self.done or self.master is None:
                return
            fcntl.ioctl(self.master, termios.TIOCSWINSZ, struct.pack('HHHH', rows, cols, 0, 0))

    def close(self):
        with self.condition:
            if self.closed:
                return
            self.closed = True
            self.condition.notify_all()
        if self.process.poll() is None:
            try:
                os.killpg(self.process.pid, signal.SIGHUP)
                self.process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(self.process.pid, signal.SIGKILL)
                    self.process.wait(timeout=3)
                except (ProcessLookupError, PermissionError, subprocess.TimeoutExpired):
                    # The reader may reap an exiting child between timeout and kill. A signal the
                    # system refuses leaves the child to the platform: close() must still return,
                    # otherwise reap_abandoned() and atexit close_all() would abort mid-loop.
                    pass
            except ProcessLookupError:
                pass
        self.reader.join(timeout=4)


def terminal_api(endpoint, data, validate_route, ssh_args):
    if endpoint == 'open':
        cols, rows = dimensions(data.get('cols', 80), data.get('rows', 24))
        if data.get('local'):
            command, cwd = local_command(data.get('path', ''))
            name = str(data.get('name') or '本地')[:100]
            local = True
        else:
            route = validate_route(data.get('route'))
            command = ssh_command(route, data.get('path', ''), ssh_args)
            cwd = None
            name = route['name']
            local = False
        with SESSIONS_LOCK:
            for identifier, session in list(SESSIONS.items()):
                if session.done and time.monotonic()-session.last_seen > 300:
                    SESSIONS.pop(identifier)
            if sum(not s.done and not s.closed for s in SESSIONS.values()) >= 8:
                raise ValueError('最多同时打开 8 个终端，请先断开一个会话')
            if local:
                session = TerminalSession(command, name, cols, rows, cwd=cwd, local=True)
            else:
                from .ssh import ssh_env
                session = TerminalSession(command, name, cols, rows, cwd=cwd, local=False, ssh_environment=ssh_env(route))
            SESSIONS[session.id] = session
        return dict(id=session.id, name=session.name, local=session.local)
    identifier = data.get('id')
    with SESSIONS_LOCK:
        session = SESSIONS.get(identifier) if isinstance(identifier, str) else None
    if not session:
        raise ValueError('终端会话不存在，请重新连接')
    if endpoint == 'poll':
        return session.poll(data.get('cursor', 0))
    if endpoint == 'write':
        if 'binary' in data:
            try:
                content = base64.b64decode(data['binary'], validate=True)
            except (ValueError, TypeError):
                raise ValueError('终端输入格式不正确')
        else:
            if not isinstance(data.get('content'), str):
                raise ValueError('终端输入格式不正确')
            content = data['content'].encode('utf-8')
        session.write(content)
    elif endpoint == 'resize':
        session.resize(data.get('cols'), data.get('rows'))
    elif endpoint == 'close':
        session.close()
    else:
        raise ValueError('终端操作不正确')
    return dict(ok=True)


def close_all():
    with SESSIONS_LOCK:
        sessions = list(SESSIONS.values())
    for session in sessions:
        session.close()


def reap_abandoned():
    # A vanished browser stops polling. Do not leave its shell or SSH process indefinitely.
    while True:
        time.sleep(30)
        with SESSIONS_LOCK:
            sessions = list(SESSIONS.values())
        for session in sessions:
            if not session.done and time.monotonic()-session.last_seen > 300:
                session.close()

threading.Thread(target=reap_abandoned, daemon=True).start()
