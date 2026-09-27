import base64
import copy
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import app
import remote_tasks
import transfer_history

class RemoteTasksTests(unittest.TestCase):
    def test_real_tmux_transfer_survives_launcher_exit_both_directions(self):
        import shutil
        if not shutil.which('tmux'):self.skipTest('tmux unavailable')
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);source=root/'source';target=root/'target';binpath=root/'bin'
            for p in (source,target,binpath,root/'home',root/'tmux'):p.mkdir()
            payload=os.urandom(256*1024);(source/'payload').write_bytes(payload)
            ssh=binpath/'ssh'
            ssh.write_text('#!'+sys.executable+'\nimport os,sys,subprocess,shlex\na=sys.argv[1:]\nwhile a and a[0].startswith("-"):\n n=2 if a[0] in ("-o","-p","-i","-J") else 1\n a=a[n:]\na=a[1:]\nif len(a)==1:a=shlex.split(a[0])\nsys.exit(subprocess.call(a))\n');ssh.chmod(0o700)
            real_rsync=app.rsync_binary();rsync=binpath/'rsync'
            rsync.write_text('#!'+sys.executable+'\nimport sys,subprocess\na=sys.argv[1:]\nif "--" in a:a=["--bwlimit=128"]+a\nsys.exit(subprocess.call(['+repr(real_rsync)+']+a))\n');rsync.chmod(0o700)
            env=dict(os.environ,HOME=str(root/'home'),TMUX_TMPDIR=str(root/'tmux'),PATH=str(binpath)+os.pathsep+os.environ['PATH'])
            for pull in (False,True):
                identifier=os.urandom(6).hex()
                if (target/'payload').exists():(target/'payload').unlink()
                peer=source if pull else target
                info=app.local_info(str(peer));script=app.REMOTE_SCRIPT
                # local_info identity is different; obtain actual REMOTE_SCRIPT identity.
                output=subprocess.check_output([sys.executable,'-c',script,str(peer),'identity'])
                identity=json.loads(output)['identity']
                config=dict(source=str(source),destination=str(target),pull=pull,target=dict(host='fixture',port=22),identity=identity,infoScript=script,items=['payload'],pack=False)
                p=dict(op='start',id=identifier,config=config,worker=Path('remote_worker.py').read_text())
                encoded=base64.b64encode(json.dumps(p).encode()).decode()
                # This launching process exits immediately; no controller remains running.
                result=subprocess.run([sys.executable,'-c',remote_tasks.BOOTSTRAP,encoded],env=env,stdout=subprocess.PIPE,stderr=subprocess.PIPE,universal_newlines=True)
                self.assertEqual(result.returncode,0,result.stderr)
                status=root/'home'/'.cache'/'termiusplus'/'jobs'/identifier/'status.json'
                deadline=time.monotonic()+12
                while time.monotonic()<deadline:
                    data=json.loads(status.read_text())
                    if data['state'] in ('completed','failed','cancelled'):break
                    time.sleep(.1)
                self.assertEqual(data['state'],'completed',data)
                self.assertEqual((target/'payload').read_bytes(),payload)
            subprocess.run(['tmux','-L','termiusplus','kill-server'],env=env,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)

    def test_restore_reattaches_instead_of_creating_new_session_and_resume_has_new_handle(self):
        with tempfile.TemporaryDirectory() as directory:
            options=dict(direction='remote',executor='destination',remoteTarget={'host':'fixture','port':22},source={'routes':[{'host':'a','name':'A'}]},destination={'routes':[{'host':'b','name':'B'}]},items=['file'])
            job=app.RemoteJob(options);job.state='transferring';job.options.update(_remoteId=job.id,_remoteSubmitted=True,_remoteConfig={})
            path=Path(directory)/'history.json';transfer_history.save(path,{job.id:job},app.LOCK)
            restored=transfer_history.restore(path,app.Job,app.RelayJob,app.RemoteJob)[job.id]
            self.assertEqual(restored.state,'transferring');calls=[]
            def call(operation,config=None):
                calls.append(operation)
                return dict(state='completed',progress=100,session='tp-'+job.id,log=[])
            with patch.object(restored,'call',side_effect=call),patch('app.checkpoint'):
                restored.run()
            self.assertEqual(calls,['poll'])
            restored.state='cancelled';resumed=app.new_transfer(restored.options,restored)
            self.assertNotIn('_remoteId',resumed.options)
            self.assertNotIn('_remoteSubmitted',resumed.options)

    def test_controller_disconnect_does_not_cancel_remote(self):
        options=dict(direction='remote',source={'routes':[{'host':'a','name':'A'}]},destination={'routes':[{'host':'b','name':'B'}]},executor='source',_remoteId='abcd',_remoteSubmitted=True)
        job=app.RemoteJob(options)
        with patch.object(job,'call',side_effect=TimeoutError('offline')),patch.object(job.monitor_stop,'wait',side_effect=lambda n:job.monitor_stop.set()):
            job.run()
        self.assertEqual(job.state,'remote_running');self.assertTrue(job.disconnected)
        self.assertFalse(job.cancel.is_set());self.assertFalse(job.snapshot()['resumable'])

if __name__=='__main__':unittest.main()
