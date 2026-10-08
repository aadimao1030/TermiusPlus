"""Cross-account transfers must authenticate separately, even on one machine."""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import app


class AccountTests(unittest.TestCase):
    def info(self, path, uid):
        script='import os;os.geteuid=lambda: '+str(uid)+'\n'+app.REMOTE_SCRIPT
        result=subprocess.run([sys.executable,'-c',script,str(path)],capture_output=True,check=True)
        return json.loads(result.stdout)

    def test_machine_shared_but_account_and_route_identity_are_distinct(self):
        with tempfile.TemporaryDirectory() as directory:
            a=self.info(directory,1001);b=self.info(directory,1002)
            alias=self.info(directory,1001)
            self.assertEqual(a['machine'],b['machine'])
            self.assertNotEqual(a['account'],b['account'])
            self.assertNotEqual(a['identity'],b['identity'])
            self.assertEqual(a['identity'],alias['identity'])
            with patch('termiusplus.filesystem.os.geteuid',return_value=1001):
                local=app.local_info(directory)
            self.assertEqual(local['account'],a['account'])

    def test_fast_route_from_other_account_is_excluded_even_in_shared_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            a=self.info(directory,1001);b=self.info(directory,1002)
            job=app.Job(dict(direction='upload',local=directory,remote=directory,
                routes=[{'host':'alice@host'},{'host':'alice@alias'},{'host':'bob@host'}]))
            def info(route,*args):return b if route['host']=='bob@host' else a
            rates={'alice@host':1048576,'alice@alias':2097152,'bob@host':100*1048576}
            with patch('app.remote_info',side_effect=info), \
                 patch('app.measure',side_effect=lambda route,*args:rates[route['host']]) as measure, \
                 patch.object(job,'command',return_value=[sys.executable,'-c','print("done")']) as command:
                job.run()
            self.assertEqual(job.state,'completed',list(job.log))
            self.assertEqual(job.route,'alice@alias')
            self.assertNotIn('bob@host',[c.args[0]['host'] for c in measure.call_args_list])
            self.assertTrue(all(c.args[1]['host']!='bob@host' for c in command.call_args_list))

    def test_frontend_defaults_cross_account_drag_to_authenticated_relay(self):
        source=(ROOT/'web/scripts/files.js').read_text()
        create=source[source.index('function routeGroup('):source.index('function transferSelected(')]
        same=source[source.index('function sameMachine('):source.index('async function moveSelection(')]
        settings=source[source.index('function updateRemoteSettings('):source.index('function saveRemoteSettings(')]
        code=r"""
const assert=require('node:assert/strict');
const routes=[{host:'alice@host'},{host:'bob@host'}];
const panes={local:{kind:'remote',connection:routes[0],root:'/alice',loadedKey:'local',machine:'same',account:'a'},
 remote:{kind:'remote',connection:routes[1],root:'/bob',loadedKey:'remote',machine:'same',account:'b'}};
const endpointKey=side=>side,endpointLabel=side=>panes[side].connection.host;
const relative=(root,path)=>path.slice(root.length+1);
const elements={},$=id=>elements[id]||=( {checked:false,value:'1',append(){},replaceChildren(){},showModal(){}} );
const node=()=>({append(){}}),rsyncReady=true,routePasswords=new Map(),remoteLinks=[];
const routePasswordKey=route=>route.host;let pending;
__SAME__
__SETTINGS__
__CREATE__
let options=createTransfer('local','remote','/alice','/bob',['/alice/folder']);
assert.equal(options.direction,'relay');assert.equal(options.crossAccount,true);
assert.equal(options.source.routes[0].host,'alice@host');assert.equal(options.destination.routes[0].host,'bob@host');
showTransfer(options,['folder']);
assert.equal($('remoteMode').value,'relay');assert.equal($('directSettings').hidden,true);
assert.match($('stageNote').textContent,/源账号读取、目标账号写入/);
// Users who have independently configured account-to-account SSH may still use remote sessions.
$('remoteMode').value='remote';updateRemoteSettings();
assert.equal($('directSettings').hidden,false);assert.equal($('remotePeer').value,'alice@host');
// Another IP/SSH alias for the same effective account retains same-account behavior.
panes.remote.account='a';assert.equal(sameAccess('local','remote'),true);
options=createTransfer('local','remote','/alice','/bob',['/alice/folder']);
assert.equal(options.crossAccount,false);showTransfer(options,['folder']);
assert.equal($('remoteMode').value,'remote');
""".replace('__SAME__',same).replace('__SETTINGS__',settings).replace('__CREATE__',create)
        result=subprocess.run(['node','-e',code],capture_output=True,text=True,timeout=10)
        self.assertEqual(result.returncode,0,result.stderr)

    def test_relay_rsync_uses_source_reader_and_target_writer_accounts(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory).resolve();source=root/'alice';target=root/'bob'
            source.mkdir(mode=0o700);target.mkdir(mode=0o700)
            (source/'folder').mkdir();(source/'folder'/'data.txt').write_text('private payload')
            (target/'keep.txt').write_text('keep')
            binary=app.rsync_binary();adapter=root/'ssh.py';log=root/'calls'
            adapter.write_text('''import sys,subprocess,shlex,os
from pathlib import Path
os.environ['PATH']=__BIN__+os.pathsep+os.environ['PATH']
a=sys.argv[1:];user=None
if a[0]=='-l':user=a[1];a=a[2:]
host=a.pop(0)
if user:host=user+'@'+host
roots=__ROOTS__;uid={'alice@host':1001,'bob@host':1002}[host]
if len(a)==1:a=shlex.split(a[0])
if '--server' in a:
    sender='--sender' in a
    if sender != (host=='alice@host'):
        print('Permission denied: isolated account',file=sys.stderr);sys.exit(13)
    mode='read' if sender else 'write'
else:
    path=a[-2] if a[-1]=='identity' else a[-1]
    if os.path.commonpath([os.path.realpath(path),roots[host]])!=roots[host]:
        print('Permission denied: other account directory',file=sys.stderr);sys.exit(13)
    mode='info'
    if '-c' in a:
        i=a.index('-c')+1;a[i]='import os;os.geteuid=lambda: '+str(uid)+'\\n'+a[i]
with open(__LOG__,'a') as f:f.write(host+' '+mode+'\\n')
sys.exit(subprocess.call(a))
'''.replace('__BIN__',repr(str(Path(binary).parent)))
                .replace('__ROOTS__',repr({'alice@host':str(source),'bob@host':str(target)}))
                .replace('__LOG__',repr(str(log))))
            a={'host':'alice@host'};b={'host':'bob@host'}
            options=dict(direction='relay',crossAccount=True,items=['folder'],auto=False,
                source=dict(kind='remote',path=str(source),routes=[a,b]),
                destination=dict(kind='remote',path=str(target),routes=[b,a]))
            with patch('app.ssh_args',return_value=[sys.executable,str(adapter)]),patch('app.checkpoint'):
                job=app.RelayJob(options);job.run()
            self.assertEqual(job.state,'completed',list(job.log))
            self.assertEqual((target/'folder'/'data.txt').read_text(),'private payload')
            self.assertEqual((source/'folder'/'data.txt').read_text(),'private payload')
            self.assertEqual((target/'keep.txt').read_text(),'keep')
            calls=log.read_text().splitlines()
            self.assertIn('alice@host read',calls);self.assertIn('bob@host write',calls)
            self.assertNotIn('alice@host write',calls);self.assertNotIn('bob@host read',calls)


if __name__=='__main__':unittest.main()
