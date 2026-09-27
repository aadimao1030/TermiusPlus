"""Serve updated UI/file actions while an older transfer service stays running."""
import argparse
import http.client
import os
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import app

class Handler(app.Handler):
    def do_POST(self):
        if self.path == '/api/files' or self.path.startswith('/api/terminal/'):
            return super().do_POST()
        expected = '127.0.0.1:%s' % self.server.server_port
        if self.headers.get('Host') != expected or self.headers.get('Authorization') != 'Bearer '+app.TOKEN or self.headers.get('Origin', 'http://'+expected) != 'http://'+expected:
            self.send_json({'error':'访问凭证或来源不正确'},403)
            return
        try:
            length = int(self.headers.get('Content-Length',0))
        except ValueError:
            self.send_json({'error':'请求长度不正确'},400)
            return
        if not 0 <= length <= 4*1024*1024:
            self.send_json({'error':'请求过大'},400)
            return
        connection = http.client.HTTPConnection('127.0.0.1',self.server.upstream_port,timeout=65)
        try:
            headers = {'Content-Type':self.headers.get('Content-Type','application/json'),'Authorization':'Bearer '+app.TOKEN}
            connection.request('POST',self.path,self.rfile.read(length),headers)
            response = connection.getresponse()
            body = response.read()
            self.send_response(response.status)
            self.send_header('Content-Type',response.getheader('Content-Type','application/json'))
            self.send_header('Content-Length',str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except (OSError,http.client.HTTPException):
            self.send_json({'error':'原服务未运行，请正常启动 app.py'},502)
        finally:
            connection.close()

if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port',type=int,default=8769)
    parser.add_argument('--upstream',type=int,default=8765)
    args=parser.parse_args()
    app.TOKEN=os.environ.get('TERMIUSPLUS_FILE_TOKEN','')
    if not app.TOKEN:
        parser.error('需要 TERMIUSPLUS_FILE_TOKEN 与原服务访问凭证一致')
    server=app.ThreadingHTTPServer(('127.0.0.1',args.port),Handler)
    server.upstream_port=args.upstream
    print('新版文件操作入口：http://127.0.0.1:%s/#%s'%(args.port,app.TOKEN),flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        app.terminal_sessions.close_all()
        server.server_close()
