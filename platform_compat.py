"""Platform helpers for process control, rsync paths, and Windows local paths.

POSIX behavior stays on the historical code path. Windows uses these helpers
so the rest of the tool can keep calling one spawn/stop/rsync interface.
"""
import os
import re
import shutil
import signal
import subprocess

# Virtual local root shown in the file pane on Windows. The same label is used
# by index.html (directoryParent / relative); keep the two in sync.
DRIVES_PATH = '此电脑'

CREATE_NEW_PROCESS_GROUP = 0x00000200
CREATE_NO_WINDOW = 0x08000000

_STYLE_CACHE = {}


def popen_extra(system=None):
    """Keyword arguments that put a child in its own process group."""
    system = os.name if system is None else system
    if system == 'nt':
        flags = getattr(subprocess, 'CREATE_NEW_PROCESS_GROUP', CREATE_NEW_PROCESS_GROUP)
        flags |= getattr(subprocess, 'CREATE_NO_WINDOW', CREATE_NO_WINDOW)
        return {'creationflags': flags}
    return {'start_new_session': True}


def hidden_run_kwargs(system=None):
    system = os.name if system is None else system
    if system == 'nt':
        return {'creationflags': getattr(subprocess, 'CREATE_NO_WINDOW', CREATE_NO_WINDOW)}
    return {}


def spawn(args, **kwargs):
    """Popen that can be signalled as a group on POSIX and via taskkill on Windows.

    start_new_session raises ValueError on Windows (Python 3.9+), so it is never
    passed there. CREATE_NEW_PROCESS_GROUP is the Windows equivalent.
    """
    extra = popen_extra()
    if os.name == 'nt':
        kwargs.pop('start_new_session', None)
        flags = kwargs.get('creationflags', 0) | extra['creationflags']
        kwargs['creationflags'] = flags
    else:
        kwargs.setdefault('start_new_session', True)
    return subprocess.Popen(args, **kwargs)


def _end_process(process):
    if not process or process.poll() is not None:
        return
    if os.name == 'nt':
        subprocess.run(
            ['taskkill', '/F', '/T', '/PID', str(process.pid)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            **hidden_run_kwargs())
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError, OSError):
        pass


def stop_process(process):
    """Ask a transfer child (and its ssh/rsync descendants) to exit."""
    _end_process(process)


def kill_process(process):
    """Stop a child that ignored the first request. On Windows both are taskkill /F /T."""
    if os.name == 'nt':
        _end_process(process)
        return
    if not process or process.poll() is not None:
        return
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError, OSError):
        pass


def install_shutdown_signals(handler):
    """Register the signals this process can actually receive."""
    for name in ('SIGHUP', 'SIGTERM', 'SIGBREAK'):
        sig = getattr(signal, name, None)
        if sig is None:
            continue
        try:
            signal.signal(sig, handler)
        except (OSError, ValueError):
            pass


def rsync_missing_message():
    if os.name == 'nt':
        return (
            '需要 rsync 3.x。Windows 没有自带可用的 rsync。'
            '推荐安装 MSYS2 后在 MSYS2 终端执行 pacman -S rsync，'
            '再设置 TERMIUSPLUS_RSYNC 为 rsync.exe 的完整路径（例如 C:\\msys64\\usr\\bin\\rsync.exe）。'
            'Git for Windows 的 usr\\bin\\rsync.exe 也可以（官方安装包默认不一定包含 rsync）。'
            'cwRsync 请同时设置 TERMIUSPLUS_RSYNC_PATH_STYLE=cygwin。'
            'MSYS2 和 Git 使用 msys 路径（默认）。'
            '未安装时仍可浏览文件和使用终端，只有传输会失败。'
        )
    return '需要 rsync 3.x。macOS 可执行 brew install rsync，然后重新启动。系统自带 openrsync 不支持本工具的进度与续传参数。'


def rsync_candidates(system=None, environ=None, which=None):
    """Search order. On macOS, Homebrew stays ahead of PATH so openrsync is not preferred."""
    system = os.name if system is None else system
    environ = os.environ if environ is None else environ
    which = shutil.which if which is None else which
    found = []

    def add(path):
        if path and path not in found:
            found.append(path)

    add(environ.get('TERMIUSPLUS_RSYNC'))
    if system == 'nt':
        add(which('rsync'))
        program_files = [
            environ.get('ProgramFiles') or r'C:\Program Files',
            environ.get('ProgramFiles(x86)') or r'C:\Program Files (x86)',
        ]
        local = environ.get('LocalAppData') or ''
        guesses = [
            r'C:\msys64\usr\bin\rsync.exe',
            r'C:\msys32\usr\bin\rsync.exe',
            r'C:\tools\msys64\usr\bin\rsync.exe',
        ]
        for root in program_files:
            guesses.append(os.path.join(root, 'Git', 'usr', 'bin', 'rsync.exe'))
            guesses.append(os.path.join(root, 'cwRsync', 'bin', 'rsync.exe'))
            guesses.append(os.path.join(root, 'cwrsync', 'bin', 'rsync.exe'))
        if local:
            guesses.append(os.path.join(local, 'Programs', 'Git', 'usr', 'bin', 'rsync.exe'))
        for guess in guesses:
            add(guess)
    else:
        add('/opt/homebrew/bin/rsync')
        add('/usr/local/bin/rsync')
        add(which('rsync'))
    return found


def rsync_env(base=None):
    env = dict(os.environ if base is None else base)
    # POSIX keeps LC_ALL=C so rsync progress stays in the C numeric format the parser expects.
    # MSYS rsync needs a UTF-8 locale or non-ASCII names fail, and must not rewrite /c/... paths.
    env['LC_ALL'] = 'C.UTF-8' if os.name == 'nt' else 'C'
    if os.name == 'nt':
        env.setdefault('MSYS_NO_PATHCONV', '1')
        env.setdefault('MSYS2_ARG_CONV_EXCL', '*')
    return env


def is_windows_path(path):
    text = str(path).replace('\\', '/')
    return bool(re.match(r'^[A-Za-z]:/', text) or re.match(r'^[A-Za-z]:$', text) or str(path).startswith('\\\\'))


def to_rsync_path(path, style):
    """Convert a Windows path into the form the chosen rsync build can open.

    Drive-letter paths cannot be passed through as C:/... because rsync treats
    the colon as a remote host. MSYS2 and Git use /c/Users/...; cwRsync uses
    /cygdrive/c/Users/... Paths that are already POSIX-shaped are unchanged so
    unit tests can keep feeding /local/a b.
    """
    raw = str(path)
    if style in ('native', None, ''):
        return raw
    text = raw.replace('\\', '/')
    if text.startswith('//'):
        return text
    match = re.match(r'^([A-Za-z]):(.*)$', text)
    if not match:
        return text
    letter = match.group(1).lower()
    rest = match.group(2)
    if not rest.startswith('/'):
        rest = '/' + rest
    if style == 'cygwin':
        return '/cygdrive/' + letter + rest
    # Any other style, including a mistaken "windows" value, uses the MSYS form.
    # A drive-letter path would be parsed by rsync as host "C".
    return '/' + letter + rest


def _detect_rsync_style(binary):
    override = os.environ.get('TERMIUSPLUS_RSYNC_PATH_STYLE', '').strip().lower()
    if override in ('msys', 'cygwin'):
        return override
    if not binary:
        return 'msys'
    try:
        folder = os.path.dirname(os.path.abspath(binary))
    except OSError:
        folder = ''
    searched = []
    if folder:
        searched.append(folder)
        parent = os.path.dirname(folder)
        if parent and parent != folder:
            searched.append(parent)
    for directory in searched:
        try:
            names = {name.lower() for name in os.listdir(directory)}
        except OSError:
            continue
        if 'cygwin1.dll' in names:
            return 'cygwin'
        if any(name.startswith('msys-') and name.endswith('.dll') for name in names):
            return 'msys'
    cygpath = None
    if folder:
        sibling = os.path.join(folder, 'cygpath.exe')
        if os.path.isfile(sibling):
            cygpath = sibling
    if not cygpath:
        found = shutil.which('cygpath')
        if found:
            cygpath = found
    if cygpath:
        try:
            out = subprocess.run(
                [cygpath, '-u', r'C:\\'], capture_output=True, text=True, timeout=5,
                **hidden_run_kwargs())
            text = (out.stdout or '').strip().lower()
            if text.startswith('/cygdrive/'):
                return 'cygwin'
            if re.match(r'/[a-z](/|$)', text):
                return 'msys'
        except (OSError, subprocess.TimeoutExpired):
            pass
    lowered = str(binary).replace('\\', '/').lower()
    if 'cygwin' in lowered or 'cwrsync' in lowered:
        return 'cygwin'
    if 'msys' in lowered or '/git/' in lowered:
        return 'msys'
    return 'msys'


def rsync_path_style(binary, system=None):
    system = os.name if system is None else system
    if system != 'nt':
        return 'native'
    override = os.environ.get('TERMIUSPLUS_RSYNC_PATH_STYLE', '').strip().lower()
    if override in ('msys', 'cygwin'):
        return override
    key = os.path.abspath(binary) if binary else ''
    cached = _STYLE_CACHE.get(key)
    if cached:
        return cached
    style = _detect_rsync_style(binary)
    _STYLE_CACHE[key] = style
    return style


def clear_rsync_style_cache():
    _STYLE_CACHE.clear()


def directory_argument(path, style='native'):
    """A directory operand for rsync, always with a trailing slash."""
    if style in ('native', None, ''):
        return str(path).rstrip('/') + '/'
    text = str(path).replace('\\', '/')
    body = to_rsync_path(text.rstrip('/'), style)
    return body + '/'


def prepare_transport_args(args, style):
    """Quote-ready ssh argv for rsync -e.

    The program rsync itself executes (ssh, or a test adapter) is converted to
    the rsync runtime's path form. The -i key stays a Windows path because the
    OpenSSH client on Windows does not understand /c/... paths.
    """
    if style in ('native', None, ''):
        return list(args)
    prepared = []
    previous = None
    for arg in args:
        text = str(arg)
        if previous == '-i':
            prepared.append(text.replace('\\', '/'))
        elif is_windows_path(text) or (
                os.name == 'nt' and os.path.isfile(text) and is_windows_path(os.path.abspath(text))):
            prepared.append(to_rsync_path(text, style))
        else:
            prepared.append(arg)
        previous = arg
    return prepared


def display_path(path):
    """Path string for the file pane. POSIX strings are returned unchanged."""
    if isinstance(path, str) and os.name != 'nt':
        return path
    text = str(path)
    if os.name != 'nt':
        return text
    text = os.path.normpath(text).replace('\\', '/')
    if re.fullmatch(r'[A-Za-z]:', text):
        text += '/'
    return text


def is_drive_list_request(path):
    if os.name != 'nt' or not isinstance(path, str):
        return False
    return path.strip() in ('', DRIVES_PATH, '/', '\\')


def list_drives():
    import string
    entries = []
    for letter in string.ascii_uppercase:
        root = '%s:\\' % letter
        if os.path.exists(root):
            entries.append(dict(name=letter + ':', path=letter + ':/', directory=True, symlink=False, size=0))
    return dict(path=DRIVES_PATH, entries=entries)


def ensure_local_directory(path):
    if is_drive_list_request(path):
        raise ValueError('请先进入某个磁盘（例如 C:/），不能把磁盘列表当作传输目录')
    root = __import__('pathlib').Path(path).expanduser().resolve(strict=True)
    if not root.is_dir():
        raise ValueError('本地路径必须是目录')
    return root


def within_directory(root, path):
    """True when path is strictly inside root. Different Windows drives are not inside."""
    if os.path.normcase(path) == os.path.normcase(root):
        return False
    try:
        common = os.path.commonpath([root, path])
    except ValueError:
        return False
    return os.path.normcase(common) == os.path.normcase(root)


def windows_shell_command(environ=None, which=None, isfile=None):
    """PowerShell when it is installed, otherwise cmd. TERMIUSPLUS_SHELL overrides both."""
    environ = os.environ if environ is None else environ
    which = shutil.which if which is None else which
    isfile = os.path.isfile if isfile is None else isfile
    custom = (environ.get('TERMIUSPLUS_SHELL') or '').strip()
    if custom:
        if not isfile(custom):
            raise ValueError('TERMIUSPLUS_SHELL 指向的程序不存在：' + custom)
        return [custom]
    powershell = which('powershell.exe') or which('pwsh.exe')
    if powershell:
        return [powershell, '-NoLogo', '-NoExit', '-Command',
                '[Console]::InputEncoding = [Console]::OutputEncoding = New-Object System.Text.UTF8Encoding $false; $OutputEncoding = [Console]::OutputEncoding']
    comspec = environ.get('COMSPEC') or which('cmd.exe')
    if comspec and isfile(comspec):
        return [comspec, '/K', 'chcp 65001>nul']
    raise ValueError('找不到本机 shell。请安装 Windows PowerShell，或设置 TERMIUSPLUS_SHELL 为 powershell.exe 或 cmd.exe 的完整路径。')


def ssh_program():
    if os.name != 'nt':
        return 'ssh'
    found = shutil.which('ssh') or shutil.which('ssh.exe')
    if found:
        return found
    system = os.path.join(os.environ.get('SystemRoot', r'C:\Windows'), 'System32', 'OpenSSH', 'ssh.exe')
    if os.path.isfile(system):
        return system
    return 'ssh'
