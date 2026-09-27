import base64
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch
if os.name == 'posix':
    import fcntl
    import struct
    import termios
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import termiusplus.server as app
import termiusplus.terminal_sessions as terminal


class TerminalTests(unittest.TestCase):
    def wait_for(self, session, expected, cursor=0):
        output=b''; deadline=time.monotonic()+5
        while time.monotonic()<deadline:
            result=session.poll(cursor,wait=.1);cursor=result['cursor']
            output+=base64.b64decode(result['content'])
            if expected in output: return output,cursor
            if result['done']: break
        self.fail('未收到 '+repr(expected)+': '+repr(output))

    @unittest.skipUnless(os.name == 'posix', 'PTY is POSIX-only')
    def test_real_pty_input_and_output_with_ctrl_c(self):
        command=[sys.executable,'-u','-c',"import sys,os,time;print('TTY',os.isatty(0),flush=True);print('INPUT',input(),flush=True);print('WAIT',flush=True);time.sleep(30)"]
        session=terminal.TerminalSession(command,'test',90,30)
        try:
            output,cursor=self.wait_for(session,b'TTY True')
            session.write('你好\r'.encode())
            output,cursor=self.wait_for(session,'INPUT 你好'.encode(),cursor)
            session.write(b'\x03')
            deadline=time.monotonic()+3
            while session.process.poll() is None and time.monotonic()<deadline: time.sleep(.03)
            self.assertIsNotNone(session.process.poll())
        finally: session.close()

    @unittest.skipUnless(os.name == 'posix', 'PTY is POSIX-only')
    def test_resize_reaches_child_and_close_ends_process(self):
        session=terminal.TerminalSession([sys.executable,'-u','-c',"import time;print('READY',flush=True);time.sleep(30)"],'resize',80,24)
        try:
            self.wait_for(session,b'READY')
            session.resize(120,40)
            rows,cols,_,_=struct.unpack('HHHH',fcntl.ioctl(session.master,termios.TIOCGWINSZ,b'\0'*8))
            self.assertEqual((cols,rows),(120,40))
            with self.assertRaises(ValueError): session.resize(9999,40)
        finally: session.close()
        self.assertIsNotNone(session.process.poll())
        with self.assertRaises(ValueError):session.write(b'no')
        session.close()

    def test_bounded_buffer_and_cursor_no_duplicate(self):
        session=terminal.TerminalSession([sys.executable,'-u','-c',"print('marker',flush=True)"],'buffer')
        try:
            self.wait_for(session,b'marker');session.reader.join(3)
            with patch('termiusplus.terminal_sessions.OUTPUT_LIMIT',32768):
                for _ in range(8): session._append(b'a'*16384)
            result=session.poll(0,wait=0)
            self.assertTrue(result['truncated'])
            self.assertLessEqual(len(base64.b64decode(result['content'])),32768)
            next_result=session.poll(result['cursor'],wait=0)
            self.assertEqual(next_result['content'],'')
            with self.assertRaises(ValueError):session.poll(result['cursor']+1,wait=0)
        finally:session.close()

    def test_ssh_command_quotes_directory_and_requests_interactive_tty(self):
        route=app.validate_route({'host':'user@example','jump':'bastion','port':2222})
        path="/tmp/a' $(touch injected); folder"
        command=terminal.ssh_command(route,path,app.ssh_args)
        self.assertIn('-tt',command)
        self.assertIn('StrictHostKeyChecking=yes',command)
        self.assertIn('BatchMode=yes',command)
        import shlex
        self.assertEqual(shlex.split(command[-1])[2],path)
        self.assertIn('"$HOME"/',terminal.ssh_command(route,'~/folder name',app.ssh_args)[-1])
        with self.assertRaises(ValueError):terminal.ssh_command(route,'bad\0path',app.ssh_args)

    @unittest.skipUnless(os.name == 'posix', 'login shell test uses /bin/sh')
    def test_terminal_api_real_shell_and_binary_input(self):
        identifier=None
        with patch('termiusplus.terminal_sessions.ssh_command',return_value=['/bin/sh','-i']):
            result=terminal.terminal_api('open',{'route':{'host':'fixture'},'cols':80,'rows':24},app.validate_route,app.ssh_args)
            identifier=result['id'];session=terminal.SESSIONS[identifier]
            try:
                terminal.terminal_api('write',{'id':identifier,'content':"printf 'TERMINAL_OK\\n'\r"},app.validate_route,app.ssh_args)
                # dash prints its prompt on the same line as the command output ($ TERMINAL_OK).
                output,cursor=self.wait_for(session,b'TERMINAL_OK\r\n')
                terminal.terminal_api('resize',{'id':identifier,'cols':100,'rows':32},app.validate_route,app.ssh_args)
                terminal.terminal_api('write',{'id':identifier,'binary':base64.b64encode(b'exit\r').decode()},app.validate_route,app.ssh_args)
                terminal.terminal_api('close',{'id':identifier},app.validate_route,app.ssh_args)
                self.assertTrue(session.closed)
            finally:session.close();terminal.SESSIONS.pop(identifier,None)

    def test_local_command_picks_login_shell_and_validates_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            if os.name == 'nt':
                shell = os.environ.get('COMSPEC', r'C:\Windows\System32\cmd.exe')
                with patch.dict(os.environ, {'TERMIUSPLUS_SHELL': shell}):
                    command, cwd = terminal.local_command(directory)
                self.assertEqual(command, [shell])
                self.assertEqual(os.path.normcase(cwd), os.path.normcase(os.path.realpath(directory)))
            else:
                with patch.dict(os.environ,{'SHELL':'/bin/sh'}):
                    command,cwd=terminal.local_command(directory)
                self.assertEqual(command,['/bin/sh','-l'])
                self.assertEqual(cwd,os.path.realpath(directory))
                with patch.dict(os.environ,{'SHELL':'relative/shell'}):
                    self.assertEqual(terminal.local_command(directory)[0][0],'/bin/sh')
            with self.assertRaises(ValueError):terminal.local_command(os.path.join(directory,'missing'))
            with self.assertRaises(ValueError):terminal.local_command(os.path.join(directory,'a\0b'))
            with self.assertRaises(ValueError):terminal.local_command(directory+'/x'*5000)
            file=Path(directory)/'file.txt';file.write_text('x')
            with self.assertRaises(ValueError):terminal.local_command(str(file))
        self.assertEqual(os.path.normcase(terminal.local_command('')[1]), os.path.normcase(os.path.realpath(os.path.expanduser('~'))))

    def test_local_session_runs_in_directory_without_ssh(self):
        with tempfile.TemporaryDirectory() as directory:
            canonical=os.path.realpath(directory)
            command=[sys.executable,'-u','-c',"import os;print('CWD',os.getcwd(),flush=True)"]
            with patch('termiusplus.terminal_sessions.local_command',return_value=(command,canonical)),\
                 patch('termiusplus.terminal_sessions.ssh_command',side_effect=AssertionError('本地终端不应使用 SSH')):
                result=terminal.terminal_api('open',{'local':True,'path':directory,'cols':90,'rows':30},app.validate_route,app.ssh_args)
                identifier=result['id'];session=terminal.SESSIONS[identifier]
                try:
                    self.assertTrue(result['local']);self.assertTrue(session.local)
                    output,cursor=self.wait_for(session,('CWD '+canonical).encode())
                    self.assertIn(b'CWD',output)
                    self.assertTrue(session.poll(cursor,wait=0)['local'])
                finally:
                    session.close();terminal.SESSIONS.pop(identifier,None)

    @unittest.skipUnless(os.name == 'nt', 'ConPTY is Windows-only')
    def test_conpty_echo_and_close(self):
        command = [sys.executable, '-u', '-c', "import os,sys;print('TTY', os.isatty(0), flush=True);print('IN', sys.stdin.readline().rstrip('\\r\\n'), flush=True)"]
        session = terminal.TerminalSession(command, 'win', 80, 24)
        try:
            self.wait_for(session, b'TTY True')
            session.write(b'hi\r\n')
            self.wait_for(session, b'IN hi')
        finally:
            session.close()
        self.assertIsNotNone(session.process.poll())


if __name__=='__main__':unittest.main()
