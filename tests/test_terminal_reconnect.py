import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import app
from termiusplus import terminal_sessions as terminal
from termiusplus import terminal_reconnect as reconnect


class ReconnectTests(unittest.TestCase):
    def run_fixture(self, outcomes):
        pipes=[];children=[]
        for code,error in outcomes:
            pipe=tempfile.TemporaryFile();pipe.write(error);pipe.seek(0);pipes.append(pipe)
            children.append(SimpleNamespace(stderr=pipe,wait=lambda code=code:code))
        stderr=io.TextIOWrapper(io.BytesIO(),encoding='utf-8')
        try:
            with patch.object(reconnect.subprocess,'Popen',side_effect=children) as launch, \
                 patch.object(reconnect.time,'sleep') as sleep, \
                 patch.object(reconnect.signal,'signal'),patch.object(reconnect.os,'isatty',return_value=False), \
                 patch.object(sys,'stderr',stderr),patch.object(sys,'stdout',io.StringIO()):
                result=reconnect.run(['ssh','fixture'])
            return result,launch,sleep
        finally:
            for pipe in pipes:pipe.close()
            stderr.close()

    def test_transport_errors_retry_same_command_but_normal_exit_stops(self):
        result,launch,sleep=self.run_fixture([(255,b'No route to host'),(255,b'Broken pipe'),(0,b'')])
        self.assertEqual(result,0);self.assertEqual(launch.call_count,3)
        self.assertEqual([c.args for c in sleep.call_args_list],[(2,),(4,)])
        self.assertTrue(all(c.args==(['ssh','fixture'],) for c in launch.call_args_list))

    def test_auth_and_host_key_failures_are_not_retried(self):
        for error in (b'Permission denied (publickey).',b'Host key verification failed.'):
            with self.subTest(error=error):
                result,launch,sleep=self.run_fixture([(255,error)])
                self.assertEqual(result,255);self.assertEqual(launch.call_count,1);sleep.assert_not_called()

    @unittest.skipUnless(shutil.which('tmux'),'tmux unavailable')
    def test_real_tmux_command_and_cwd_survive_transport_loss(self):
        from test_terminal import TerminalTests
        waiter=TerminalTests()
        with tempfile.TemporaryDirectory(dir='/tmp',prefix='tp-term-') as directory:
            root=Path(directory).resolve()
            for name in ('home','sockets','cwd'): (root/name).mkdir()
            env=dict(os.environ,HOME=str(root/'home'),TMUX_TMPDIR=str(root/'sockets'))
            env.pop('TMUX',None)
            name='tp-'+'b'*32
            route=app.validate_route({'host':'fixture'})
            bootstrap=terminal.ssh_command(route,str(root),app.ssh_args,name)[-1]
            fake=root/'transport.py'
            fake.write_text('''import os,sys,subprocess,time
from pathlib import Path
root=Path(sys.argv[1]);counter=root/'attempts'
attempt=int(counter.read_text())+1 if counter.exists() else 1
counter.write_text(str(attempt))
child=subprocess.Popen(['/bin/sh','-c',sys.argv[2]],stderr=sys.stdout)
while child.poll() is None:
    if attempt==1 and (root/'drop').exists():
        child.terminate();child.wait()
        print('Read from remote host: No route to host',file=sys.stderr,flush=True)
        sys.exit(255)
    time.sleep(.02)
sys.exit(child.returncode)
''')
            command=[sys.executable,str(fake),str(root),bootstrap]
            session=terminal.TerminalSession(['--reconnect-ssh',json.dumps(command)],'reconnect',ssh_environment=env)
            tmux=['tmux','-L','termiusplus-terminal']
            def run_tmux(*args):
                return subprocess.run(tmux+list(args),env=env,capture_output=True,text=True)
            def await_file(path):
                deadline=time.monotonic()+8
                while not path.exists() and time.monotonic()<deadline:time.sleep(.03)
                self.assertTrue(path.exists(),str(path))
            try:
                deadline=time.monotonic()+5
                while run_tmux('has-session','-t',name).returncode and time.monotonic()<deadline:time.sleep(.03)
                self.assertEqual(run_tmux('has-session','-t',name).returncode,0,
                                 session.poll(0,wait=0))
                pane=run_tmux('display-message','-p','-t',name,'#{pane_id}').stdout
                session.write(b"export TP_MARKER=kept; cd cwd; touch ../started; sleep 3; printf 'kept' > ../finished; printf '\\nJOB_SURVIVED\\n'\r")
                await_file(root/'started');(root/'drop').touch()
                output,cursor=waiter.wait_for(session,'SSH 线路已断开'.encode())
                await_file(root/'finished')
                waiter.wait_for(session,b'JOB_SURVIVED',cursor)
                session.write(b"printf '\\nSTATE:%s:%s\\n' \"$TP_MARKER\" \"$PWD\"\r")
                waiter.wait_for(session,('STATE:kept:'+str(root/'cwd')).encode())
                session.write(b'sleep 30\r')
                time.sleep(.15)
                session.write(b'\x03')
                session.write(b"printf '\\nCTRL_C_OK\\n'\r")
                waiter.wait_for(session,b'\r\nCTRL_C_OK\r\n')
                self.assertGreaterEqual(int((root/'attempts').read_text()),2)
                self.assertEqual(run_tmux('display-message','-p','-t',name,'#{pane_id}').stdout,pane)
                self.assertFalse(session.done)
                session.close()
                self.assertEqual(run_tmux('has-session','-t',name).returncode,0)
            finally:
                session.close();run_tmux('kill-server')


if __name__=='__main__':unittest.main()
