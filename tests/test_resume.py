import copy
import json
import os
import re
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import app
import platform_compat
import transfer_history as history


class ResumeTests(unittest.TestCase):
    def test_cancelled_relay_restored_and_real_partial_reused(self):
        binary = app.rsync_binary()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); source = root/'source'; target = root/'target'
            source.mkdir(); target.mkdir(); payload = os.urandom(2*1024*1024)
            (source/'payload').write_bytes(payload)
            options = dict(direction='relay',items=['payload'], source=dict(kind='remote',path=str(source),routes=[{'host':'fixture'}]),destination=dict(kind='remote',path=str(target),routes=[{'host':'fixture'}]))
            original = app.Job.command
            def command(job, binary, route, canonical, local):
                args = original(job,binary,route,canonical,local)
                style = platform_compat.rsync_path_style(binary)
                src = platform_compat.directory_argument(str(source), style)
                dst = platform_compat.directory_argument(str(target), style)
                loc = platform_compat.directory_argument(local, style)
                args[-2:] = [src, loc] if job.options['direction']=='download' else [loc, dst]
                return args[:1]+(['--bwlimit=128'] if slow[0] else ['--stats'])+args[1:]
            slow=[True]
            with patch('app.Job.command',command),patch('app.remote_info',side_effect=lambda r,p,*a:dict(path=p,identity='fixture')),patch('app.HISTORY_PATH',root/'history.json'),patch.dict(app.JOBS,{},clear=True):
                first=app.RelayJob(copy.deepcopy(options));app.JOBS[first.id]=first
                thread=threading.Thread(target=first.run);thread.start()
                deadline=time.monotonic()+8
                while (not first.child or first.child.state!='transferring') and time.monotonic()<deadline:time.sleep(.02)
                self.assertIsNotNone(first.child)
                time.sleep(1.2);first.cancel.set();app.stop_process(first.process);thread.join(8)
                self.assertFalse(thread.is_alive());self.assertEqual(first.state,'cancelled')
                cache=Path(first.options['_relayDirectory'])
                self.assertTrue((cache/'.termiusplus-partial'/'payload').exists())
                app.checkpoint()
                recovered=history.restore(root/'history.json',app.Job,app.RelayJob)[first.id]
                second=app.new_transfer(options,recovered)
                self.assertEqual(second.options['_relayDirectory'],str(cache))
                slow[0]=False;second.run()
                self.assertEqual(second.state,'completed',list(second.log))
                self.assertEqual((target/'payload').read_bytes(),payload)
                matched=[int(m.group(1).replace(',','')) for line in second.log if (m:=re.search(r'Matched data: ([\d,]+) bytes',line))]
                self.assertTrue(any(n>0 for n in matched),list(second.log))
                self.assertFalse(cache.exists())

    def test_interrupted_restore_and_identity_not_affected_by_transfer_settings(self):
        with tempfile.TemporaryDirectory() as directory:
            job=app.Job(dict(direction='upload',routes=[{'host':'fixture'}],local='/tmp/source',remote='/tmp/target',items=['file'],auto=True))
            job.state='transferring'
            path=Path(directory)/'history.json'
            history.save(path,{job.id:job},app.LOCK)
            restored=history.restore(path,app.Job,app.RelayJob)[job.id]
            self.assertEqual(restored.state,'interrupted')
            self.assertTrue(restored.snapshot()['resumable'])
            options=copy.deepcopy(job.options);options['auto']=False;options['threshold']=5
            self.assertEqual(history.transfer_key(options),job.transfer_key)
            options['remote']='/different'
            self.assertNotEqual(history.transfer_key(options),job.transfer_key)

    def test_prepared_archive_is_reused_and_stage_rehydrated(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);(root/'file').write_text('pack')
            first=app.Job(dict(direction='upload',routes=[{'host':'fixture'}],local=directory,remote='/tmp/target',items=['file'],pack=True,stage='fixture'))
            key=first.transfer_key
            with patch('app.HISTORY_PATH',root/'history.json'),patch.dict(app.JOBS,{},clear=True):
                first.pack(root=directory);first.state='cancelled'
                archive=first.result['archive']
                self.assertFalse(first.options['pack'])
                with patch.dict(app.STAGES,{},clear=True):
                    resumed=app.new_transfer(first.options,first)
                    self.assertFalse(resumed.options['pack'])
                    self.assertEqual(resumed.options['items'],[Path(archive).name])
                    self.assertEqual(resumed.transfer_key,key)
                    self.assertTrue(app.STAGES['fixture']['busy'])
                    self.assertFalse(resumed.cancel.is_set())

if __name__=='__main__':unittest.main()
