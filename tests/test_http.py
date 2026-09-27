import base64
import http.client
import json
import os
import tempfile
import threading
import time
import unittest
import urllib.parse
from pathlib import Path
from unittest.mock import patch
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import app


class HttpTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = app.ThreadingHTTPServer(('127.0.0.1', 0), app.Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown(); cls.server.server_close(); cls.thread.join()

    def request(self, path, data=None, body=None, headers=None):
        connection = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=10)
        request_headers = {'Authorization': 'Bearer '+app.TOKEN}
        request_headers.update(headers or {})
        if body is None:
            body = json.dumps(data or {}).encode()
            request_headers['Content-Type'] = 'application/json'
        connection.request('POST', path, body=body, headers=request_headers)
        response = connection.getresponse(); result = json.loads(response.read())
        status = response.status; connection.close()
        return status, result

    def test_file_actions_and_extracted_assets(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'sample';path.write_text('keep')
            payload=dict(side='local',root=directory,path=str(path),operation='rename',name='renamed')
            status,_=self.request('/api/files',payload,headers={'Authorization':'Bearer wrong'})
            self.assertEqual(status,403);self.assertTrue(path.exists())
            status,result=self.request('/api/files',payload)
            self.assertEqual(status,200,result);self.assertEqual(Path(result['path']).read_text(),'keep')
            payload.update(path=result['path'],operation='trash')
            status,result=self.request('/api/files',payload)
            self.assertEqual(status,200,result);self.assertEqual(Path(result['path']).read_text(),'keep')
        for endpoint,content in [('/','/static/scripts/files.js'),('/terminal','/static/scripts/terminal.js'),('/static/scripts/files.js','showFileMenu'),('/static/styles/files.css','.file-menu')]:
            connection=http.client.HTTPConnection('127.0.0.1',self.server.server_port,timeout=10)
            connection.request('GET',endpoint);response=connection.getresponse()
            self.assertEqual(response.status,200);self.assertIn(content,response.read().decode());connection.close()

    def test_auth_and_origin_validation(self):
        status, _ = self.request('/api/status', headers={'Authorization': 'Bearer wrong'})
        self.assertEqual(status, 403)
        status, _ = self.request('/api/status', headers={'Origin': 'https://example.com'})
        self.assertEqual(status, 403)
        status, result = self.request('/api/status')
        self.assertEqual(status, 200)
        self.assertTrue(result['rsync']['ok'])

    def test_terminal_assets_and_authenticated_shell_endpoint(self):
        from unittest.mock import patch
        from termiusplus import terminal_sessions
        connection = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=10)
        connection.request('GET', '/terminal')
        response = connection.getresponse()
        self.assertEqual(response.status, 200)
        self.assertIn(b'xterm.js', response.read())
        connection.close()
        with patch('termiusplus.terminal_sessions.ssh_command', return_value=['/bin/sh', '-i']):
            status, result = self.request('/api/terminal/open', {'route':{'host':'fixture'},'cols':80,'rows':24})
            self.assertEqual(status, 200, result)
            identifier = result['id']
            try:
                status, _ = self.request('/api/terminal/write', {'id':identifier,'content':'echo HTTP_TERMINAL\r'})
                self.assertEqual(status, 200)
                status, output = self.request('/api/terminal/poll', {'id':identifier,'cursor':0})
                self.assertEqual(status, 200)
                self.assertIn('cursor', output)
                status, _ = self.request('/api/terminal/resize', {'id':identifier,'cols':120,'rows':40})
                self.assertEqual(status, 200)
                status, _ = self.request('/api/terminal/poll', {'id':identifier}, headers={'Authorization':'Bearer wrong'})
                self.assertEqual(status, 403)
            finally:
                self.request('/api/terminal/close', {'id':identifier})
                terminal_sessions.SESSIONS.pop(identifier, None)

    def test_terminal_local_shell_endpoint(self):
        from termiusplus import terminal_sessions
        with tempfile.TemporaryDirectory() as directory:
            canonical = os.path.realpath(directory)
            command = [sys.executable, '-u', '-c', "import os;print('LOCAL_CWD',os.getcwd(),flush=True)"]
            with patch('termiusplus.terminal_sessions.local_command', return_value=(command, canonical)):
                status, result = self.request('/api/terminal/open', {'local':True,'path':directory,'cols':80,'rows':24})
                self.assertEqual(status, 200, result)
                self.assertTrue(result['local'])
                identifier = result['id']
                try:
                    session = terminal_sessions.SESSIONS[identifier]
                    output = b''; cursor = 0; deadline = time.monotonic()+5
                    while time.monotonic() < deadline and ('LOCAL_CWD '+canonical).encode() not in output:
                        status, polled = self.request('/api/terminal/poll', {'id':identifier,'cursor':cursor})
                        self.assertEqual(status, 200)
                        output += base64.b64decode(polled['content']); cursor = polled['cursor']
                        time.sleep(.05)
                    self.assertIn(('LOCAL_CWD '+canonical).encode(), output)
                    self.assertTrue(session.local)
                finally:
                    self.request('/api/terminal/close', {'id':identifier})
                    terminal_sessions.SESSIONS.pop(identifier, None)

    def test_resume_endpoint_and_same_transfer_start_reuse(self):
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory); source=root/'source'; target=root/'target'
            source.mkdir(); target.mkdir(); (source/'file').write_text('resume')
            options=dict(direction='copy',local=str(source),destination=str(target),items=['file'])
            previous=app.Job(options.copy());previous.state='cancelled'
            with patch.dict(app.JOBS,{previous.id:previous},clear=True),patch('app.HISTORY_PATH',root/'history.json'),patch('app.launch'):
                status,result=self.request('/api/resume',{'id':previous.id})
                self.assertEqual(status,200,result)
                resumed=app.JOBS[result['id']]
                self.assertEqual(resumed.options['_resumedFrom'],previous.id)
                self.assertFalse(resumed.cancel.is_set())
                status,_=self.request('/api/resume',{'id':previous.id})
                self.assertEqual(status,400) # No concurrent writer.
                resumed.state='cancelled'
                status,result=self.request('/api/start',dict(options,_relayDirectory='/must/not/be/used'))
                self.assertEqual(status,200,result)
                again=app.JOBS[result['id']]
                self.assertEqual(again.options['_resumedFrom'],resumed.id)
                self.assertNotIn('_relayDirectory',again.options)
                again.state='completed'
                status,_=self.request('/api/resume',{'id':again.id})
                self.assertEqual(status,400)

    def test_preview_endpoint_is_read_only(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d)/'example.txt'; p.write_text('preview sample')
            status, result = self.request('/api/preview', {'side':'local', 'path':str(p)})
            self.assertEqual(status, 200)
            self.assertEqual(result['kind'], 'text')
            self.assertEqual(result['content'], 'preview sample')
            self.assertEqual(p.read_text(), 'preview sample')
            status, _ = self.request('/api/preview', {'side':'invalid', 'path':str(p)})
            self.assertEqual(status, 400)

    def test_large_binary_drop_request(self):
        status, created = self.request('/api/stage/create')
        self.assertEqual(status, 200)
        identifier = created['id']
        try:
            payload = b'example' * 50000
            query = urllib.parse.urlencode({'id': identifier, 'path': 'folder name/资料.dat', 'offset': 0})
            status, result = self.request('/api/stage/chunk?'+query, body=payload)
            self.assertEqual(status, 200, result)
            status, sealed = self.request('/api/stage/seal', {'id': identifier})
            self.assertEqual(status, 200)
            self.assertEqual((Path(sealed['path'])/'folder name'/'资料.dat').read_bytes(), payload)
            status, result = self.request('/api/stage/chunk?'+query, body=b'again')
            self.assertEqual(status, 400)
        finally:
            self.request('/api/stage/release', {'id': identifier})

    def test_pack_endpoint_reports_archive_result(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d)/'selected.txt').write_text('selected')
            status, result = self.request('/api/pack', {'side': 'local', 'local': d, 'items': ['selected.txt']})
            self.assertEqual(status, 200, result)
            identifier = result['id']
            try:
                deadline = time.monotonic()+5
                while app.JOBS[identifier].state not in ('completed', 'failed') and time.monotonic()<deadline:
                    time.sleep(.05)
                status, result = self.request('/api/status')
                job = next(j for j in result['jobs'] if j['id'] == identifier)
                self.assertEqual(job['state'], 'completed', job['error'])
                self.assertTrue(Path(job['result']['archive']).exists())
            finally:
                app.JOBS.pop(identifier)

if __name__ == '__main__':
    unittest.main()
