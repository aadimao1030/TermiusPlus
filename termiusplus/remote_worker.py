"""Runs on the executor server in a detached tmux session; Python 3.6 compatible."""
import collections
import json
import os
import re
import shlex
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

TERMINAL = ('completed','failed','cancelled','interrupted')

def write_json(path, value):
    temporary=path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value,ensure_ascii=False));os.chmod(str(temporary),0o600)
    os.replace(str(temporary),str(path))

def run(directory):
    directory=Path(directory);config=json.loads((directory/'config.json').read_text())
    state=dict(state='checking',progress=0,speed=0,transferredBytes=0,eta='',error='',log=[],session=config['session'])
    logs=collections.deque(maxlen=120);child=[None];cancelled=threading.Event()
    def save():
        state['log']=list(logs);write_json(directory/'status.json',state)
    def event(text):
        logs.append(time.strftime('%H:%M:%S')+' '+text);save()
    def stop(*args):
        cancelled.set()
        if child[0] and child[0].poll() is None:
            try:os.killpg(child[0].pid,signal.SIGTERM)
            except ProcessLookupError:pass
    signal.signal(signal.SIGTERM,stop);signal.signal(signal.SIGHUP,stop);signal.signal(signal.SIGINT,stop)
    def watch():
        while not cancelled.wait(.25):
            if (directory/'cancel').exists():stop();return
    threading.Thread(target=watch,daemon=True).start()
    try:
        if cancelled.is_set():return
        target=config['target'];ssh=['ssh','-o','BatchMode=yes','-o','StrictHostKeyChecking=yes','-o','ForwardAgent=no','-o','IdentityAgent=none','-o','ConnectTimeout=10','-o','ServerAliveInterval=5','-o','ServerAliveCountMax=3','-p',str(target['port'])]
        if target.get('key'):ssh+=['-i',os.path.expanduser(target['key'])]
        info_command='python3 -c '+shlex.quote(config['infoScript'])+' '+shlex.quote(config['source'] if config.get('pull') else config['destination'])+' identity'
        result=subprocess.run(ssh+[target['host'],info_command],stdout=subprocess.PIPE,stderr=subprocess.PIPE,universal_newlines=True,timeout=25)
        if result.returncode:raise ValueError(('B 无法独立登录 A：' if config.get('pull') else 'A 无法独立登录 B：')+result.stderr[-1000:])
        info=json.loads(result.stdout)
        if info['identity']!=config['identity']:raise ValueError('远程会话登录的服务器或目录与本机选择不同，拒绝传输')
        if not re.search(r'rsync\s+version\s+[3-9]\.',info.get('rsyncVersion','')):raise ValueError('另一台服务器需要 rsync 3.x')
        version=subprocess.run(['rsync','--version'],stdout=subprocess.PIPE,universal_newlines=True)
        if not re.search(r'rsync\s+version\s+[3-9]\.',version.stdout):raise ValueError('会话执行端需要 rsync 3.x')
        items=config.get('items')
        if config.get('pack'):
            state['state']='packing';event('在 A 上打包')
            pack_args=[sys.executable,'-c',config['archiveScript'],config['source'],json.dumps(items),config['archiveName']]
            if config.get('pull'):pack_args=ssh+[target['host'],' '.join(shlex.quote(arg) for arg in pack_args)]
            child[0]=subprocess.Popen(pack_args,stdout=subprocess.PIPE,stderr=subprocess.PIPE,universal_newlines=True,start_new_session=True)
            if cancelled.is_set():stop()
            output,error=child[0].communicate()
            if child[0].returncode:raise ValueError(error[-1000:])
            archive=json.loads(output)['path'];items=[Path(archive).name]
            config.update(pack=False,items=items);write_json(directory/'config.json',config)
            state['result']={'archive':archive}
        selection=[]
        if items:
            if config.get('flattenItems') and len({Path(item).name for item in items}) != len(items):
                raise ValueError('所选项目包含同名文件或文件夹，请分开传输或先打包')
            manifest=directory/'files';manifest.write_bytes(b''.join(item.encode('utf-8')+b'\0' for item in items))
            selection=['-r','--from0','--files-from='+str(manifest)]
            if config.get('flattenItems'):selection.append('--no-relative')
        transport=' '.join(shlex.quote(arg) for arg in ssh)
        source=config['source'].rstrip('/')+'/'
        destination=config['destination'].rstrip('/')+'/'
        if config.get('pull'):source=target['host']+':'+info['path'].rstrip('/')+'/'
        else:destination=target['host']+':'+info['path'].rstrip('/')+'/'
        command=['rsync','-a','-s','--no-owner','--no-group','--partial-dir=.termiusplus-partial','--info=progress2','--no-inc-recursive','--outbuf=L','--timeout=30',*selection,'-e',transport,'--',source,destination]
        for attempt in range(4):
            if cancelled.is_set():break
            state['state']='transferring';event('A → B 直传；tmux 会话 '+config['session'])
            child[0]=subprocess.Popen(command,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,start_new_session=True,env=dict(os.environ,LC_ALL='C'))
            if cancelled.is_set():stop()
            buffer=b''
            def record(line):
                text=line.decode(errors='replace').strip()
                if not text:return
                match=re.match(r'^([\d,]+)\s+(\d+)%\s+([\d.,]+)\s*([kKMGT]?)B/s\s+(\S+)',text)
                if match:
                    state.update(progress=min(100,int(match[2])),transferredBytes=int(match[1].replace(',','')),speed=float(match[3].replace(',',''))*1024**({'':0,'K':1,'M':2,'G':3,'T':4}[match[4].upper()]),eta=match[5])
                event(text)
            while True:
                block=os.read(child[0].stdout.fileno(),4096)
                if not block:break
                lines=re.split(rb'[\r\n]',buffer+block);buffer=lines.pop()
                for line in lines:record(line)
            record(buffer);child[0].stdout.close();code=child[0].wait()
            if cancelled.is_set():break
            if not code:
                state.update(state='completed',progress=100,speed=0);event('远程直传完成');return
            if code not in (10,12,30,35,255) or attempt==3:raise ValueError('rsync 退出码 '+str(code)+'，未完成数据保留在目标服务器')
            state['state']='retrying';event('A → B 连接故障，远程自动重试 '+str(attempt+1)+'/3');cancelled.wait(2)
    except Exception as exc:
        state.update(state='failed',error=str(exc),speed=0);event(str(exc))
    finally:
        if cancelled.is_set():state.update(state='cancelled',speed=0);event('远程任务已取消，断点数据保留')
        save();cancelled.set()

if __name__=='__main__':run(sys.argv[1])
