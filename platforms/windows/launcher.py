"""Windows desktop launcher.

Starts app.py on 127.0.0.1 with a random token and opens Edge or Chrome in
--app mode with an isolated profile. Closing that window stops the server this
launcher started. A server this launcher already left running is reused and is
not stopped when a second window hands off to the same profile.

Run it with pythonw (scripts/TermiusPlus.vbs) so there is no console. Plain
`python platforms/windows/launcher.py` works too. This is the source-tree app; it
does not replace `python app.py`.
"""
import ctypes
import http.client
import json
import os
import secrets
import socket
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from termiusplus import platform_compat  # noqa: E402


def data_root(environ=None):
    environ = os.environ if environ is None else environ
    local = environ.get('LOCALAPPDATA') or str(Path.home() / 'AppData' / 'Local')
    return Path(local) / 'TermiusPlus'


def console_python():
    """pythonw cannot host a server that should keep writing a log. Prefer python.exe beside it."""
    exe = Path(sys.executable)
    if exe.name.lower() in ('pythonw.exe', 'pyw.exe'):
        sibling = exe.with_name('python.exe')
        if sibling.is_file():
            return str(sibling)
    return sys.executable


def port_open(port, timeout=0.2):
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(timeout)
            return sock.connect_ex(('127.0.0.1', int(port))) == 0
    except OSError:
        return False


def choose_port(start, is_open, span=50):
    start = int(start)
    for port in range(start, start + span):
        if not is_open(port):
            return port
    raise RuntimeError('从 %s 起的 %s 个端口都已被占用' % (start, span))


def token_accepted(port, token, timeout=2):
    try:
        connection = http.client.HTTPConnection('127.0.0.1', int(port), timeout=timeout)
        connection.request('POST', '/api/status', b'{}', {
            'Authorization': 'Bearer ' + str(token),
            'Content-Type': 'application/json',
            'Host': '127.0.0.1:%s' % port,
        })
        response = connection.getresponse()
        response.read()
        connection.close()
        return response.status == 200
    except OSError:
        return False


def pid_alive(pid):
    if not pid or os.name != 'nt':
        return False
    handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, int(pid))
    if not handle:
        return False
    ctypes.windll.kernel32.CloseHandle(handle)
    return True


def should_reuse(state, project, pid_is_alive, port_is_open, token_works):
    """Reuse only a server this launcher recorded for the same project directory."""
    if not isinstance(state, dict) or state.get('project') != str(project):
        return False
    try:
        port = int(state.get('port'))
    except (TypeError, ValueError):
        return False
    token = state.get('token')
    if not isinstance(token, str) or not token:
        return False
    if not pid_is_alive(state.get('pid')) or not port_is_open(port):
        return False
    return bool(token_works(port, token))


def find_browser(environ=None, isfile=None, which=None):
    environ = os.environ if environ is None else environ
    isfile = os.path.isfile if isfile is None else isfile
    which = __import__('shutil').which if which is None else which
    custom = environ.get('TERMIUSPLUS_BROWSER') or ''
    if custom and isfile(custom):
        return custom
    program_files = environ.get('PROGRAMFILES') or r'C:\Program Files'
    program_files_x86 = environ.get('PROGRAMFILES(X86)') or r'C:\Program Files (x86)'
    local = environ.get('LOCALAPPDATA') or ''
    candidates = [
        os.path.join(program_files, 'Microsoft', 'Edge', 'Application', 'msedge.exe'),
        os.path.join(program_files_x86, 'Microsoft', 'Edge', 'Application', 'msedge.exe'),
        os.path.join(local, 'Google', 'Chrome', 'Application', 'chrome.exe') if local else '',
        os.path.join(program_files, 'Google', 'Chrome', 'Application', 'chrome.exe'),
        os.path.join(program_files_x86, 'Google', 'Chrome', 'Application', 'chrome.exe'),
    ]
    for candidate in candidates:
        if candidate and isfile(candidate):
            return candidate
    for name in ('msedge.exe', 'chrome.exe', 'msedge', 'chrome'):
        found = which(name)
        if found:
            return found
    return None


def browser_command(browser, url, profile):
    return [
        browser,
        '--app=' + url,
        '--user-data-dir=' + str(profile),
        '--no-first-run',
        '--no-default-browser-check',
        '--disable-extensions',
        '--disable-sync',
    ]


def load_state(path):
    try:
        data = json.loads(Path(path).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def save_state(path, state):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state), encoding='utf-8')


def alert(text):
    if os.name != 'nt':
        print(text, file=sys.stderr)
        return
    ctypes.windll.user32.MessageBoxW(None, str(text), 'TermiusPlus', 0x00000010)


def copy_text(text):
    if os.name != 'nt':
        return
    try:
        subprocess.run(
            ['powershell', '-NoProfile', '-NonInteractive', '-Command', 'Set-Clipboard -Value ([Console]::In.ReadToEnd())'],
            input=text, text=True, **platform_compat.hidden_run_kwargs())
    except OSError:
        pass


def start_server(project, port, token, log_path):
    log_path = Path(log_path)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log = open(log_path, 'ab', buffering=0)
    try:
        env = os.environ.copy()
        env['TERMIUSPLUS_TOKEN'] = token
        env['PYTHONUNBUFFERED'] = '1'
        env['PYTHONIOENCODING'] = 'utf-8'
        return subprocess.Popen(
            [console_python(), '-u', str(Path(project) / 'app.py'), '--port', str(port)],
            cwd=str(project), env=env, stdin=subprocess.DEVNULL,
            stdout=log, stderr=subprocess.STDOUT,
            creationflags=platform_compat.popen_extra('nt')['creationflags'])
    finally:
        log.close()


def wait_until_listening(port, process, timeout=30):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if process.poll() is not None:
            raise RuntimeError('服务进程已退出，请查看日志')
        if port_open(port):
            return
        time.sleep(0.1)
    raise RuntimeError('服务在 30 秒内没有开始监听 127.0.0.1:%s' % port)


def stop_server(process):
    platform_compat.stop_process(process)
    try:
        process.wait(timeout=8)
    except subprocess.TimeoutExpired:
        platform_compat.kill_process(process)
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass


def _release_owned_server(server, state_file):
    if server is not None and server.poll() is None:
        stop_server(server)
    try:
        Path(state_file).unlink()
    except OSError:
        pass


def run(project=None):
    project = Path(project or ROOT)
    root = data_root()
    state_file = root / 'server.json'
    log_path = root / 'server.log'
    profile = root / 'browser-profile'
    state = load_state(state_file)
    server = None
    owned = True
    try:
        if should_reuse(state, project, pid_alive, port_open, token_accepted):
            port = int(state['port'])
            token = state['token']
            owned = False
        else:
            port = choose_port(int(os.environ.get('TERMIUSPLUS_PORT', '8765')), port_open)
            token = secrets.token_urlsafe(32)
            server = start_server(project, port, token, log_path)
            wait_until_listening(port, server)
            save_state(state_file, {'pid': server.pid, 'port': port, 'token': token, 'project': str(project)})
        url = 'http://127.0.0.1:%s/#%s' % (port, token)
        try:
            with open(log_path, 'a', encoding='utf-8') as log:
                log.write(url + '\n')
        except OSError:
            pass
        browser = find_browser()
        if not browser:
            copy_text(url)
            try:
                os.startfile(url)  # noqa: the Windows-only API opens the default browser
            except OSError:
                pass
            alert('没有找到 Microsoft Edge 或 Google Chrome，无法打开独立窗口。\n已尝试用默认浏览器打开，链接也已复制：\n%s\n\n点确定后，本次由 App 启动的服务会停止。日志：%s' % (url, log_path))
            return server, owned
        profile.mkdir(parents=True, exist_ok=True)
        window = subprocess.Popen(browser_command(browser, url, profile))
        window.wait()
        return server, owned
    except Exception:
        if owned:
            _release_owned_server(server, state_file)
        raise


def main():
    if os.name != 'nt':
        print('platforms/windows/launcher.py 只用于 Windows。请运行 python3 app.py，或在 macOS 使用 TermiusPlus.app。', file=sys.stderr)
        return 2
    server = None
    owned = False
    try:
        server, owned = run()
    except Exception as exc:
        alert(str(exc))
        return 1
    finally:
        if owned and server is not None and server.poll() is None:
            stop_server(server)
            try:
                (data_root() / 'server.json').unlink()
            except OSError:
                pass
    return 0


if __name__ == '__main__':
    sys.exit(main())
