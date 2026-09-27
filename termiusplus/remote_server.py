"""Separate remote-task controller while an older local relay stays running."""
import json
import os
import sys
import urllib.request
from . import server as app

token=os.environ.get('TERMIUSPLUS_REMOTE_TOKEN')
if not token:raise SystemExit('请设置 TERMIUSPLUS_REMOTE_TOKEN 为文件服务的访问凭证')
app.TOKEN=token

def check_local_transfer():
    request=urllib.request.Request('http://127.0.0.1:8765/api/status',data=b'{}',headers={'Authorization':'Bearer '+token,'Content-Type':'application/json'})
    try:
        data=json.load(urllib.request.urlopen(request,timeout=5))
    except OSError:
        raise ValueError('无法核对本机传输状态，请确认原文件服务在线，避免重复写入目标')
    if any(j['state'] not in ('completed','failed','cancelled','interrupted') for j in data['jobs']):
        raise ValueError('原文件服务仍有任务，请先完成或取消后再启动远程会话')
app.REMOTE_START_CHECK=check_local_transfer
sys.argv=['app.py','--port','8768']
app.main()
