"""Explicit opt-in setup of a dedicated B -> A key; never copies a Mac private key."""
import argparse
import hashlib
import json
import shlex
import subprocess
import app

CREATE_KEY = r'''
import os,sys,subprocess
from pathlib import Path
root=Path.home()/'.ssh';root.mkdir(mode=0o700,exist_ok=True)
key=root/sys.argv[1]
if not key.exists():
 subprocess.run(['ssh-keygen','-q','-t','ed25519','-N','','-C',sys.argv[1],'-f',str(key)],check=True)
print(key.with_suffix('.pub').read_text().strip())
'''
ADD_KEY = r'''
import os,sys
from pathlib import Path
public=sys.argv[1].strip()
if not public.startswith('ssh-ed25519 ') or len(public.split())<2:raise ValueError('Invalid public key')
root=Path.home()/'.ssh';root.mkdir(mode=0o700,exist_ok=True)
path=root/'authorized_keys';existing=path.read_text() if path.exists() else ''
if not any(public.split()[1] in line.split() for line in existing.splitlines()):
 with path.open('a') as output:
  if existing and not existing.endswith('\n'):output.write('\n')
  output.write('restrict '+public+'\n')
 os.chmod(str(path),0o600)
print('Authorized dedicated transfer key; forwarding and PTY disabled')
'''

def ssh(route, script, argument):
    command='python3 -c '+shlex.quote(script)+' '+shlex.quote(argument)
    result=subprocess.run(app.ssh_args(route)+['-o','ForwardAgent=no',route['host'],command],stdout=subprocess.PIPE,stderr=subprocess.PIPE,universal_newlines=True,timeout=30)
    if result.returncode:raise ValueError(result.stderr[-1500:])
    return result.stdout.strip()

def install(source, executor):
    name='termiusplus-'+hashlib.sha256((source['host']+':'+str(source['port'])).encode()).hexdigest()[:16]
    public=ssh(executor,CREATE_KEY,name)
    ssh(source,ADD_KEY,public)
    return '~/.ssh/'+name

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source',required=True,help='A connection JSON')
    parser.add_argument('--executor',required=True,help='B connection JSON')
    parser.add_argument('--install',action='store_true',help='Explicitly authorize creating a B-owned key and granting it A login access')
    args=parser.parse_args();source=app.validate_route(json.loads(args.source));executor=app.validate_route(json.loads(args.executor))
    if args.install:print(json.dumps({'key':install(source,executor),'host':source['host'],'port':source['port']}))
    else:print('将仅在 B 创建专用密钥，并将其公钥以 restrict 选项加入 A 的 authorized_keys。B 可执行 A 账号下的命令；不允许此密钥进行端口转发、agent 转发或分配 PTY。需要明确授权后才加 --install。')
