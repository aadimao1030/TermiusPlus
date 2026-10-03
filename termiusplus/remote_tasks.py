"""Control detached remote tmux jobs. Losing the controller never cancels a job."""
import base64
import json
import re
import shlex
import subprocess
import threading
import time
from pathlib import Path

BOOTSTRAP = r'''
import os,sys,json,base64,subprocess,shlex,shutil
from pathlib import Path
p=json.loads(base64.b64decode(sys.argv[1]));identifier=p['id']
if not identifier or any(c not in '0123456789abcdef' for c in identifier):raise ValueError('Invalid job id')
root=Path.home()/'.cache'/'termiusplus'/'jobs'/identifier;status=root/'status.json';session='tp-'+identifier
command=['tmux','-L','termiusplus','-f','/dev/null']
def output(value):print(json.dumps(value,ensure_ascii=False))
if p['op']=='start':
 if not shutil.which('tmux'):raise ValueError('执行端需要 tmux，请先安装')
 root.mkdir(parents=True,exist_ok=True);os.chmod(str(root),0o700)
 if not status.exists():
  config=p['config'];config['session']=session
  (root/'config.json').write_text(json.dumps(config));os.chmod(str(root/'config.json'),0o600)
  (root/'worker.py').write_text(p['worker']);os.chmod(str(root/'worker.py'),0o600)
  status.write_text(json.dumps(dict(state='queued',progress=0,speed=0,log=[],error='')))
  env=dict(os.environ);env.pop('SSH_AUTH_SOCK',None)
  shell='exec '+shlex.quote(sys.executable)+' '+shlex.quote(str(root/'worker.py'))+' '+shlex.quote(str(root))
  result=subprocess.run(command+['new-session','-d','-s',session,shell],stdout=subprocess.PIPE,stderr=subprocess.PIPE,env=env)
  if result.returncode:
   status.unlink();raise ValueError(result.stderr.decode(errors='replace'))
 output(dict(session=session))
elif p['op']=='cancel':
 if not status.exists():raise ValueError('远程任务不存在')
 state=json.loads(status.read_text())
 if state['state'] not in ('completed','failed','cancelled','interrupted'):(root/'cancel').touch()
 output(dict(ok=True))
else:
 if not status.exists():raise ValueError('远程记录不存在，可能已被清理')
 state=json.loads(status.read_text())
 if state['state'] not in ('completed','failed','cancelled','interrupted'):
  alive=subprocess.run(command+['has-session','-t',session],stdout=subprocess.PIPE,stderr=subprocess.PIPE).returncode==0
  if not alive:state.update(state='interrupted',error='远程会话已退出或服务器已重启，可续传')
 state['session']=session;output(state)
'''


class RemoteRejected(ValueError):pass

def make_job_class(BaseJob, remote_info, ssh_args, checkpoint, base, info_script, archive_script, archive_name):
    class RemoteJob(BaseJob):
        def __init__(self, options):
            super().__init__(options)
            self.monitor_stop=threading.Event()
            self.disconnected=False
            self.remote_session=''

        def route_config(self):
            return self.options['destination' if self.options.get('executor')=='destination' else 'source']['routes'][0]

        def call(self, operation, config=None):
            payload=dict(op=operation,id=self.options.get('_remoteId',self.id))
            if config is not None:payload.update(config=config,worker=(Path(base)/'remote_worker.py').read_text())
            encoded=base64.b64encode(json.dumps(payload).encode()).decode()
            command='python3 -c '+shlex.quote(BOOTSTRAP)+' '+shlex.quote(encoded)
            from .ssh import ssh_env
            result=subprocess.run(ssh_args(self.route_config())+['-o','ForwardAgent=no',self.route_config()['host'],command],stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=35,env=ssh_env(self.route_config()))
            if result.returncode:
                error=result.stderr.decode(errors='replace')[-1500:] or '无法联系远程执行端'
                raise (RemoteRejected if result.returncode!=255 else ValueError)(error)
            return json.loads(result.stdout)

        def snapshot(self):
            result=super().snapshot()
            result.update(remoteSession=self.remote_session or 'tp-'+self.options.get('_remoteId',self.id),executor=self.options.get('executor','source'),disconnected=self.disconnected)
            return result

        def request_cancel(self):
            if not self.options.get('_remoteId'):
                self.cancel.set();return
            self.call('cancel')
            self.event('已向远程会话发送取消请求，等待确认')

        def run(self):
            if not self.options.get('_remoteId'):
                try:
                    self.state='checking'
                    source=remote_info(self.options['source']['routes'][0],self.options['source']['path'],True)
                    target=remote_info(self.options['destination']['routes'][0],self.options['destination']['path'],True)
                    pull=self.options.get('executor')=='destination'
                    peer=source if pull else target
                    config=dict(source=source['path'],destination=target['path'],pull=pull,target=self.options['remoteTarget'],identity=peer['identity'],infoScript=info_script,items=self.options.get('items'),flattenItems=bool(self.options.get('flattenItems')),pack=bool(self.options.get('pack')),archiveScript=archive_script)
                    if config['pack']:config['archiveName']=archive_name(config['items'])
                    if self.cancel.is_set():self.state='cancelled';return
                    # Persist the handle BEFORE submitting: ambiguous SSH acknowledgements must not launch twice.
                    self.options['_remoteId']=self.id;self.options['_remoteConfig']=config
                    self.state='remote_running';checkpoint()
                except Exception as exc:
                    self.state='failed';self.error=str(exc);self.event(self.error);return
            launched=bool(self.options.get('_remoteSubmitted'))
            while not self.monitor_stop.is_set():
                try:
                    if not launched:
                        self.remote_session=self.call('start',self.options['_remoteConfig'])['session']
                        launched=True;self.options['_remoteSubmitted']=True
                        self.event('远程独立会话已启动：'+self.remote_session+'；Mac 断联不影响传输')
                        checkpoint()
                    status=self.call('poll')
                    self.disconnected=False;self.remote_session=status['session']
                    self.state=status['state'];self.error=status.get('error','')
                    self.progress=status.get('progress',0);self.speed=status.get('speed',0)
                    self.transferred_bytes=status.get('transferredBytes',0);self.eta=status.get('eta','')
                    self.log.clear();self.log.extend(status.get('log',[]))
                    self.result=status.get('result')
                    if self.result and self.result.get('archive'):
                        self.options['pack']=False;self.options['items']=[Path(self.result['archive']).name]
                    self.route=self.options['source']['routes'][0]['name']+' → '+self.options['destination']['routes'][0]['name']+' · 远程会话'
                    checkpoint()
                    if self.state in ('completed','failed','cancelled','interrupted'):return
                except RemoteRejected as exc:
                    if not launched:
                        self.state='failed';self.error=str(exc);self.event(self.error);return
                    self.disconnected=True;self.state='remote_running';self.error=str(exc)
                except Exception as exc:
                    self.disconnected=True;self.state='remote_running';self.speed=0
                    self.error='本地暂时无法读取远程进度；远程任务未被取消：'+str(exc)
                self.monitor_stop.wait(5)
    return RemoteJob
