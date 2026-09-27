import os
import re
import py_compile
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import termiusplus.platform_compat as platform_compat
import platforms.windows.launcher as windows_app


ROOT = Path(__file__).resolve().parents[1]


class WindowsPathTests(unittest.TestCase):
    def test_rsync_paths_avoid_drive_colon(self):
        self.assertEqual(platform_compat.to_rsync_path(r'C:\Users\me\a', 'msys'), '/c/Users/me/a')
        self.assertEqual(platform_compat.to_rsync_path('C:/Users/me/a/', 'msys'), '/c/Users/me/a/')
        self.assertEqual(platform_compat.to_rsync_path('D:/data', 'cygwin'), '/cygdrive/d/data')
        self.assertEqual(platform_compat.to_rsync_path('/local/a b', 'msys'), '/local/a b')
        self.assertEqual(platform_compat.to_rsync_path('//server/share/dir', 'msys'), '//server/share/dir')
        self.assertEqual(platform_compat.directory_argument('/local/a b', 'native'), '/local/a b/')
        self.assertEqual(platform_compat.directory_argument(r'C:\work\src', 'msys'), '/c/work/src/')
        self.assertFalse(re.search(r'^[A-Za-z]:', platform_compat.to_rsync_path(r'C:\Users\me\a', 'windows')))

    def test_transport_converts_program_but_not_key(self):
        args = [r'C:\Windows\System32\OpenSSH\ssh.exe', '-i', r'C:\Users\me\.ssh\id', '-p', '22']
        prepared = platform_compat.prepare_transport_args(args, 'msys')
        self.assertEqual(prepared[0], '/c/Windows/System32/OpenSSH/ssh.exe')
        self.assertEqual(prepared[2], 'C:/Users/me/.ssh/id')
        self.assertEqual(platform_compat.prepare_transport_args(args, 'native'), args)

    def test_drive_letter_style_override_is_ignored(self):
        platform_compat.clear_rsync_style_cache()
        with patch.dict(os.environ, {'TERMIUSPLUS_RSYNC_PATH_STYLE': 'windows'}):
            style = platform_compat.rsync_path_style('rsync-not-installed', 'nt')
        self.assertIn(style, ('msys', 'cygwin'))
        self.assertNotEqual(style, 'windows')

    def test_posix_candidate_order_prefers_homebrew_before_path(self):
        environ = {'TERMIUSPLUS_RSYNC': '/custom/rsync'}
        paths = platform_compat.rsync_candidates('posix', environ, which=lambda name: '/usr/bin/rsync')
        self.assertEqual(paths[0], '/custom/rsync')
        self.assertLess(paths.index('/opt/homebrew/bin/rsync'), paths.index('/usr/bin/rsync'))

    def test_windows_candidates_include_env_and_known_locations(self):
        environ = {'TERMIUSPLUS_RSYNC': r'C:\tools\rsync.exe', 'ProgramFiles': r'C:\Program Files', 'LocalAppData': r'C:\Users\me\AppData\Local'}
        paths = platform_compat.rsync_candidates('nt', environ, which=lambda name: r'C:\path\rsync.exe')
        self.assertEqual(paths[0], r'C:\tools\rsync.exe')
        self.assertIn(r'C:\msys64\usr\bin\rsync.exe', paths)
        self.assertIn(os.path.join(r'C:\Program Files', 'Git', 'usr', 'bin', 'rsync.exe'), paths)
        self.assertIn(r'C:\path\rsync.exe', paths)

    def test_display_path_and_drive_request(self):
        self.assertEqual(platform_compat.display_path('/tmp/a'), '/tmp/a')
        with patch('termiusplus.platform_compat.os.name', 'nt'):
            self.assertEqual(platform_compat.display_path(r'C:\Users\me'), 'C:/Users/me')
            self.assertEqual(platform_compat.display_path('C:'), 'C:/')
            self.assertTrue(platform_compat.is_drive_list_request('此电脑'))
            self.assertTrue(platform_compat.is_drive_list_request(''))
            self.assertFalse(platform_compat.is_drive_list_request(r'C:\Users'))

    def test_shell_selection_and_missing_custom_shell(self):
        environ = {}
        command = platform_compat.windows_shell_command(environ, which=lambda name: r'C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe' if name == 'powershell.exe' else None, isfile=lambda path: True)
        self.assertEqual(command[0], r'C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe')
        self.assertIn('-NoLogo', command)
        cmd = platform_compat.windows_shell_command({'COMSPEC': r'C:\Windows\System32\cmd.exe'}, which=lambda name: None, isfile=lambda path: path.endswith('cmd.exe'))
        self.assertEqual(cmd[:2], [r'C:\Windows\System32\cmd.exe', '/K'])
        with self.assertRaises(ValueError):
            platform_compat.windows_shell_command({'TERMIUSPLUS_SHELL': r'C:\missing.exe'}, which=lambda name: None, isfile=lambda path: False)
        with self.assertRaises(ValueError):
            platform_compat.windows_shell_command({}, which=lambda name: None, isfile=lambda path: False)

    def test_spawn_flags_do_not_use_start_new_session_on_windows(self):
        self.assertEqual(platform_compat.popen_extra('posix'), {'start_new_session': True})
        extra = platform_compat.popen_extra('nt')
        self.assertNotIn('start_new_session', extra)
        self.assertTrue(extra['creationflags'] & platform_compat.CREATE_NEW_PROCESS_GROUP)
        self.assertTrue(extra['creationflags'] & platform_compat.CREATE_NO_WINDOW)

    def test_within_directory_rejects_other_drive(self):
        self.assertTrue(platform_compat.within_directory('/work', '/work/a'))
        self.assertFalse(platform_compat.within_directory('/work', '/work'))
        self.assertFalse(platform_compat.within_directory('/work', '/other'))
        self.assertFalse(platform_compat.within_directory(r'C:\work', r'D:\work\a'))

    def test_conpty_module_compiles_without_executing(self):
        py_compile.compile(str(ROOT / 'platforms' / 'windows' / 'conpty.py'), doraise=True)

    def test_frontend_parent_and_relative_paths(self):
        html = (ROOT / 'web' / 'index.html').read_text(encoding='utf-8')
        start = html.index('function relative(')
        end = html.index('function updateSelection(')
        script = html[start:end] + r"""
const assert=require('node:assert/strict');
assert.equal(relative('/','/tmp/a'), 'tmp/a');
assert.equal(relative('/home/me','/home/me/a b'), 'a b');
assert.equal(relative('C:/','C:/Windows'), 'Windows');
assert.equal(relative('C:/Users','C:/Users/me'), 'me');
assert.throws(()=>relative('此电脑','C:/'));
assert.equal(directoryParent('/home/me'), '/home');
assert.equal(directoryParent('/home'), '/');
assert.equal(directoryParent('/'), '/');
assert.equal(directoryParent('~'), '~');
assert.equal(directoryParent('C:/Users/me'), 'C:/Users');
assert.equal(directoryParent('C:/Users'), 'C:/');
assert.equal(directoryParent('C:/'), '此电脑');
assert.equal(directoryParent('此电脑'), '此电脑');
assert.equal(directoryParent('C:\\Users\\me'), 'C:/Users');
"""
        subprocess.run(['node', '-e', script], check=True, capture_output=True)


class WindowsLauncherTests(unittest.TestCase):
    def test_browser_command_is_app_mode_with_isolated_profile(self):
        command = windows_app.browser_command(r'C:\Edge\msedge.exe', 'http://127.0.0.1:8765/#token', r'C:\profile')
        self.assertEqual(command[0], r'C:\Edge\msedge.exe')
        self.assertTrue(command[1].startswith('--app=http://127.0.0.1:8765/#'))
        self.assertIn('--user-data-dir=' + r'C:\profile', command)
        self.assertIn('--no-first-run', command)

    def test_find_browser_prefers_env_then_edge(self):
        found = windows_app.find_browser(
            {'TERMIUSPLUS_BROWSER': r'C:\custom\edge.exe', 'PROGRAMFILES': r'C:\Program Files'},
            isfile=lambda path: path == r'C:\custom\edge.exe', which=lambda name: None)
        self.assertEqual(found, r'C:\custom\edge.exe')
        found = windows_app.find_browser(
            {'PROGRAMFILES': r'C:\Program Files', 'PROGRAMFILES(X86)': r'C:\Program Files (x86)', 'LOCALAPPDATA': r'C:\Users\me\AppData\Local'},
            isfile=lambda path: path.endswith('msedge.exe'), which=lambda name: None)
        self.assertTrue(found.endswith('msedge.exe'))

    def test_choose_port_skips_open_ports(self):
        self.assertEqual(windows_app.choose_port(8765, lambda port: port < 8767), 8767)
        with self.assertRaises(RuntimeError):
            windows_app.choose_port(1, lambda port: True, span=2)

    def test_reuse_requires_same_project_live_pid_and_token(self):
        project = r'C:\src\TermiusPlus'
        state = {'project': project, 'pid': 10, 'port': 8765, 'token': 'abc'}
        self.assertTrue(windows_app.should_reuse(state, project, lambda pid: True, lambda port: True, lambda port, token: token == 'abc'))
        self.assertFalse(windows_app.should_reuse(state, r'C:\other', lambda pid: True, lambda port: True, lambda port, token: True))
        self.assertFalse(windows_app.should_reuse(state, project, lambda pid: False, lambda port: True, lambda port, token: True))
        self.assertFalse(windows_app.should_reuse(state, project, lambda pid: True, lambda port: False, lambda port, token: True))
        self.assertFalse(windows_app.should_reuse(state, project, lambda pid: True, lambda port: True, lambda port, token: False))
        self.assertFalse(windows_app.should_reuse(None, project, lambda pid: True, lambda port: True, lambda port, token: True))

    def test_state_roundtrip(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'server.json'
            windows_app.save_state(path, {'pid': 3, 'port': 8765, 'token': 't', 'project': directory})
            self.assertEqual(windows_app.load_state(path)['token'], 't')


if __name__ == '__main__':
    unittest.main()
