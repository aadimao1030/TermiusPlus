"""Password authentication must not leak into saved routes or rsync arguments."""
import json
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from termiusplus import ssh, transfer_history, server


class PasswordTests(unittest.TestCase):
    def test_remote_timeout_does_not_expose_ssh_command(self):
        route = dict(name='slow', host='user@slow.example', port=22, jump='', key='')
        with patch.object(server.subprocess, 'run', side_effect=subprocess.TimeoutExpired(['ssh', 'private-command'], 60)):
            with self.assertRaisesRegex(ValueError, 'SSH 连接或远程目录读取超时') as failure:
                server.remote_info(route, '~')
        self.assertNotIn('private-command', str(failure.exception))

    def test_askpass_and_checkpoint_do_not_contain_password(self):
        raw = dict(name='sample', host='user@password.example', port=2200,
                   jump='', key='', password='test-secret-42')
        route = ssh.validate_route(raw)
        self.assertNotIn('password', route)
        env = ssh.ssh_env(route)
        self.assertEqual(subprocess.check_output([env['SSH_ASKPASS']], env=env).strip(), b'test-secret-42')
        self.assertIn('BatchMode=no', ssh.ssh_args(route))
        self.assertIn('NumberOfPasswordPrompts=1', ssh.ssh_args(route))
        self.assertNotIn(raw['password'], ' '.join(ssh.ssh_args(route)))
        with tempfile.TemporaryDirectory() as directory:
            job = server.Job(dict(direction='upload', routes=[route], local=directory, remote='~', items=['x']))
            history = Path(directory) / 'history.json'
            transfer_history.save(history, {job.id: job}, threading.RLock())
            self.assertNotIn(raw['password'], history.read_text())
        ssh.validate_route(dict(raw, password=''))
        self.assertIn('BatchMode=yes', ssh.ssh_args(route))
        self.assertIsNone(ssh.ssh_env(route))

    def test_browser_routes_are_secret_free(self):
        script = (ROOT / 'web/scripts/files.js').read_text()
        helpers = script[script.index('const routePasswords = new Map();'):script.index('let routes = [],')]
        checks = r'''
const assert=require('node:assert/strict');
const raw={host:'user@server',port:22,jump:'',key:'',password:'secret'};
const saved=rememberRoute(raw);
assert.equal(JSON.stringify(saved).includes('secret'),false);
assert.equal(withRoutePasswords(saved).password,'secret');
assert.equal(withRoutePasswords({source:{routes:[saved]}}).source.routes[0].password,'secret');
'''
        result = subprocess.run(['node', '-e', helpers + checks], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == '__main__':
    unittest.main()
