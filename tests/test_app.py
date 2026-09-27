import importlib.util
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import app

class CoreTests(unittest.TestCase):
    def test_reject_command_hosts(self):
        for host in ['-oProxyCommand=evil', 'host;touch /tmp/a', 'user@host $(id)', 'host\nwhoami']:
            with self.assertRaises(ValueError):
                app.validate_route({'host': host})

    def test_ssh_options_are_arguments(self):
        route = app.validate_route({'host': 'user@server', 'jump': 'user@gateway:2222', 'key': '/tmp/key $(id)', 'port': 2200})
        args = app.ssh_args(route)
        self.assertIn('/tmp/key $(id)', args)
        self.assertIn('StrictHostKeyChecking=yes', args)
        self.assertIn('BatchMode=yes', args)

    def test_remote_path_is_shell_quoted(self):
        route = app.validate_route({'host': 'server'})
        result = subprocess.CompletedProcess([], 0, b'{"entries":[],"path":"/tmp","identity":"x"}', b'')
        with patch('app.subprocess.run', return_value=result) as run:
            app.remote_info(route, "/tmp/a' $(touch evil)")
            command = run.call_args.args[0][-1]
            import shlex
            self.assertEqual(shlex.split(command)[-1], "/tmp/a' $(touch evil)")

    def test_transfer_has_no_deletion_and_protects_paths(self):
        route = app.validate_route({'host': 'server'})
        job = app.Job({'direction': 'upload'})
        command = job.command('/bin/rsync', route, '/remote/a $(id)', '/local/a b')
        self.assertIn('-s', command)
        self.assertIn('--partial-dir=.termiusplus-partial', command)
        self.assertNotIn('--delete', command)
        self.assertNotIn('--inplace', command)
        self.assertEqual(command[-2:], ['/local/a b/', 'server:/remote/a $(id)/'])
        job.options['direction'] = 'download'
        self.assertEqual(job.command('/bin/rsync', route, '/remote', '/local')[-2:], ['server:/remote/', '/local/'])

    def test_switch_requires_material_improvement(self):
        self.assertFalse(app.should_switch(1048576, 1100000))
        self.assertFalse(app.should_switch(1000, 2000))
        self.assertTrue(app.should_switch(1048576, 2 * 1048576))

    def test_local_directory_symlink_can_be_browsed(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root/'nested').mkdir()
            (root/'link').symlink_to(root/'nested', target_is_directory=True)
            result = app.local_info(d)
            link = next(e for e in result['entries'] if e['name'] == 'link')
            self.assertTrue(link['directory'])
            (root/'nested'/'file').write_text('content')
            inside = app.local_info(link['path'])
            self.assertEqual(inside['path'], str((root/'nested').resolve()))
            self.assertEqual(inside['entries'][0]['name'], 'file')
            self.assertTrue(link['symlink'])

    def test_remote_directory_symlink_and_broken_link(self):
        import json
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); (root/'target').mkdir(); (root/'target'/'file').write_text('sample')
            (root/'projects').symlink_to(root/'target', target_is_directory=True)
            (root/'broken').symlink_to(root/'missing')
            (root/'file-link').symlink_to(root/'target'/'file')
            def listing(path):
                return json.loads(subprocess.run([sys.executable, '-c', app.REMOTE_SCRIPT, str(path)], capture_output=True, check=True).stdout)
            entries = {e['name']:e for e in listing(root)['entries']}
            self.assertTrue(entries['projects']['directory'])
            self.assertTrue(entries['projects']['symlink'])
            self.assertFalse(entries['broken']['directory'])
            self.assertFalse(entries['file-link']['directory'])
            self.assertEqual(listing(root/'projects')['entries'][0]['name'], 'file')
            local = {e['name']:e for e in app.local_info(root)['entries']}
            self.assertTrue(local['projects']['directory'])
            self.assertFalse(local['broken']['directory'])

    def test_real_rsync_merge(self):
        try:
            binary = app.rsync_binary()
        except ValueError as exc:
            self.skipTest(str(exc))
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); source = root/'source'; target = root/'target'
            source.mkdir(); target.mkdir()
            (source/'new file').write_text('new')
            (target/'keep').write_text('preserved')
            (source/'nested').mkdir(); (source/'nested'/'file').write_text('nested')
            job = app.Job({'direction': 'upload'})
            command = job.command(binary, {'host': 'server'}, '/unused', str(source))
            command[-1] = str(target) + '/'
            subprocess.run(command, check=True, capture_output=True)
            self.assertEqual((target/'new file').read_text(), 'new')
            self.assertEqual((target/'nested'/'file').read_text(), 'nested')
            self.assertEqual((target/'keep').read_text(), 'preserved')

    def test_real_interrupted_transfer_resumes(self):
        import os
        import time
        try:
            binary = app.rsync_binary()
        except ValueError as exc:
            self.skipTest(str(exc))
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); source = root/'source'; target = root/'target'
            source.mkdir(); target.mkdir()
            payload = os.urandom(1024 * 1024)
            (source/'payload').write_bytes(payload)
            job = app.Job({'direction': 'upload'})
            command = job.command(binary, {'host': 'server'}, '/unused', str(source))
            command[-1] = str(target) + '/'
            slow = command[:1] + ['--bwlimit=64'] + command[1:]
            process = subprocess.Popen(slow, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
            try:
                time.sleep(1.2)
            finally:
                app.stop_process(process)
                process.wait(timeout=5)
            self.assertTrue((target/'.termiusplus-partial'/'payload').exists())
            subprocess.run(command, check=True, capture_output=True)
            self.assertEqual((target/'payload').read_bytes(), payload)

    def test_remote_listing_script_handles_special_names(self):
        import json
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "a' $(id)\nfile").write_text('sample')
            result = subprocess.run([sys.executable, '-c', app.REMOTE_SCRIPT, d], capture_output=True, check=True)
            info = json.loads(result.stdout)
            self.assertEqual(info['entries'][0]['name'], "a' $(id)\nfile")
            self.assertEqual(len(info['identity']), 64)

    def test_job_chooses_fastest_verified_route(self):
        with tempfile.TemporaryDirectory() as d:
            job = app.Job({'direction': 'upload', 'local': d, 'remote': '/remote', 'routes': [{'host': 'slow'}, {'host': 'different'}, {'host': 'fast'}]})
            def info(route, *args):
                return {'identity': 'wrong' if route['host'] == 'different' else 'same', 'path': '/remote'}
            def speed(route, *args):
                return 1000 if route['host'] == 'slow' else 2000000
            with patch('app.rsync_binary', return_value='rsync'), patch('app.remote_info', side_effect=info), patch('app.measure', side_effect=speed), patch.object(job, 'command', return_value=[sys.executable, '-c', 'print("done")']):
                job.run()
            self.assertEqual(job.state, 'completed')
            self.assertEqual(job.route, 'fast')
            self.assertTrue(any('已排除' in line for line in job.log))

    def test_network_error_retries_on_alternative(self):
        with tempfile.TemporaryDirectory() as d:
            job = app.Job({'direction': 'download', 'local': d, 'remote': '/remote', 'routes': [{'host': 'first'}, {'host': 'backup'}]})
            def command(binary, route, *args):
                return [sys.executable, '-c', 'import sys;sys.exit(%d)' % (12 if route['host'] == 'first' else 0)]
            with patch('app.rsync_binary', return_value='rsync'), patch('app.remote_info', return_value={'identity': 'same', 'path': '/remote'}), patch('app.measure', side_effect=lambda r, *a: 2000 if r['host'] == 'first' else 1000), patch.object(job, 'command', side_effect=command):
                job.run()
            self.assertEqual(job.state, 'completed')
            self.assertEqual(job.route, 'backup')
            self.assertTrue(any('重试' in line for line in job.log))

    def test_probe_timeout_keeps_verified_route(self):
        with tempfile.TemporaryDirectory() as d:
            job = app.Job(dict(direction='upload', local=d, remote='/remote', routes=[{'host':'primary'}, {'host':'other'}]))
            def info(route, *args):
                return dict(identity='same' if route['host']=='primary' else 'different', path='/remote')
            with patch('app.rsync_binary', return_value='rsync'), patch('app.remote_info', side_effect=info), patch('app.measure', side_effect=TimeoutError('probe timeout')), patch.object(job, 'command', return_value=[sys.executable,'-c','pass']):
                job.run()
            self.assertEqual(job.state, 'completed', list(job.log))
            self.assertEqual(job.route, 'primary')
            self.assertTrue(any('保留线路' in line for line in job.log))

    def test_retry_skips_unreachable_backup(self):
        with tempfile.TemporaryDirectory() as d:
            job = app.Job(dict(direction='upload', local=d, remote='/remote', routes=[{'host':'primary'}, {'host':'backup'}]))
            checks = dict(primary=0, backup=0); transfers=[]
            def info(route, *args):
                checks[route['host']] += 1
                if route['host']=='backup' and checks['backup']>1: raise TimeoutError('backup offline')
                return dict(identity='same',path='/remote')
            def command(binary,route,*args):
                transfers.append(route['host'])
                return [sys.executable,'-c','import sys;sys.exit(%d)' % (12 if len(transfers)==1 else 0)]
            with patch('app.rsync_binary', return_value='rsync'), patch('app.remote_info', side_effect=info), patch('app.measure', side_effect=lambda r,*a: 2000 if r['host']=='primary' else 1000), patch.object(job,'command',side_effect=command):
                job.run()
            self.assertEqual(job.state,'completed',list(job.log))
            self.assertEqual(transfers,['primary','primary'])
            self.assertTrue(any('暂不可用' in line for line in job.log))

    def test_unreachable_primary_never_promotes_unknown_server(self):
        with tempfile.TemporaryDirectory() as d:
            job=app.Job(dict(direction='upload',local=d,remote='/remote',routes=[{'host':'primary'},{'host':'unrelated'}]))
            with patch('app.rsync_binary',return_value='rsync'), patch('app.remote_info',side_effect=TimeoutError('offline')) as info, patch.object(job.cancel,'wait',return_value=False), patch.object(job,'command') as command:
                job.run()
            self.assertEqual(job.state,'failed')
            self.assertEqual(info.call_count,4)
            self.assertTrue(all(call.args[0]['host']=='primary' for call in info.call_args_list))
            command.assert_not_called()

    def test_remote_dependency_check_uses_python36_subprocess_options(self):
        import contextlib, io, json
        from types import SimpleNamespace
        with tempfile.TemporaryDirectory() as d:
            output=io.StringIO()
            def old_run(*args,**kwargs):
                self.assertNotIn('capture_output',kwargs)
                self.assertNotIn('text',kwargs)
                return SimpleNamespace(stdout='rsync version 3.2.7')
            with patch.object(sys,'argv',['remote',d,'identity']), patch('subprocess.run',side_effect=old_run), contextlib.redirect_stdout(output):
                exec(app.REMOTE_SCRIPT,{})
            self.assertEqual(json.loads(output.getvalue())['rsyncVersion'],'rsync version 3.2.7')

    def test_cancel_stops_process_group(self):
        process = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'], start_new_session=True)
        app.stop_process(process)
        self.assertNotEqual(process.wait(timeout=3), 0)

if __name__ == '__main__':
    unittest.main()
