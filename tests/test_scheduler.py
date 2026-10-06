import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import app
from termiusplus.scheduler import Resource, TransferScheduler, conflicts, resources


class HeldJob(app.Job):
    def __init__(self, options, failure=False):
        super().__init__(options)
        self.entered = threading.Event()
        self.release = threading.Event()
        self.failure = failure

    def run(self):
        self.state = 'transferring'
        self.entered.set()
        if not self.release.wait(5): raise TimeoutError('test release')
        if self.failure: raise ValueError('test failure')
        self.state = 'completed'


class SchedulerTests(unittest.TestCase):
    def wait_for(self, predicate):
        deadline = time.monotonic() + 3
        while not predicate() and time.monotonic() < deadline:
            time.sleep(.01)
        self.assertTrue(predicate())

    def test_conflicting_fifo_and_independent_parallel_tasks(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            def options(item): return dict(direction='copy',local=str(root/'source'),destination=str(root/'target'),items=[item])
            scheduler = TransferScheduler(lambda o: resources(o, None), lambda: None)
            a, b, c = [HeldJob(options(item)) for item in ['folder', 'folder/child', 'other']]
            threads = []
            try:
                threads.append(scheduler.submit(a)); self.assertTrue(a.entered.wait(2))
                threads.append(scheduler.submit(b))
                threads.append(scheduler.submit(c)); self.assertTrue(c.entered.wait(2))
                self.wait_for(lambda: a.id in b.queue_reason)
                self.assertFalse(b.entered.is_set())
                self.assertEqual(b.state, 'queued')
                a.release.set(); self.assertTrue(b.entered.wait(2))
                self.assertEqual(b.queue_reason, '')
            finally:
                for job in (a,b,c): job.release.set()
                for thread in threads: thread.join(3)
                scheduler.close()
            self.assertTrue(all(not t.is_alive() for t in threads))

    def test_waiting_cancel_and_failed_writer_release_next_task(self):
        options = dict(direction='copy',local='/source',destination='/target',items=['file'])
        scheduler = TransferScheduler(lambda o: resources(o, None), lambda: None)
        a, b, c = HeldJob(options, failure=True), HeldJob(options), HeldJob(options)
        threads = []
        try:
            threads.append(scheduler.submit(a)); self.assertTrue(a.entered.wait(2))
            threads.append(scheduler.submit(b)); threads.append(scheduler.submit(c))
            self.wait_for(lambda: a.id in b.queue_reason)
            scheduler.cancel(b)
            self.wait_for(lambda: b.state == 'cancelled')
            self.assertFalse(b.entered.is_set())
            a.release.set(); self.assertTrue(c.entered.wait(2))
            self.assertEqual(a.state,'failed')
        finally:
            for job in (a,b,c): job.release.set()
            for thread in threads: thread.join(3)
            scheduler.close()

    def test_reads_and_sibling_files_share_but_read_write_and_subtrees_conflict(self):
        r = lambda p,w: [Resource('local',p,w)]
        self.assertFalse(conflicts(r('/data',False),r('/data/file',False)))
        self.assertFalse(conflicts(r('/data/a',True),r('/data/b',True)))
        self.assertFalse(conflicts(r('/data',True),r('/database',True)))
        self.assertTrue(conflicts(r('/data',True),r('/data/file',False)))
        self.assertTrue(conflicts(r('/data/file',True),r('/data',False)))
        self.assertFalse(conflicts(r('/data',True),[Resource('remote:machine','/data',True)]))
        self.assertTrue(conflicts([Resource('remote:*','/',False)],[Resource('remote:machine','/data',True)]))

    def test_local_symlinks_remote_ip_aliases_and_flattened_selections(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); (root/'real').mkdir(); (root/'alias').symlink_to(root/'real',target_is_directory=True)
            left = resources(dict(direction='copy',local='/source',destination=str(root/'real'),items=['parent/file'],flattenItems=True),None)
            right = resources(dict(direction='copy',local='/source',destination=str(root/'alias'),items=['file']),None)
            self.assertTrue(conflicts(left,right))
            self.assertTrue(any(r.write and r.path==str(root/'real/file') for r in left))
            def resolver(endpoint,items):
                data = subprocess.run([sys.executable,'-c',app.RESOURCE_SCRIPT],input=json.dumps(dict(path=endpoint['path'],items=items)).encode(),capture_output=True,check=True)
                info = json.loads(data.stdout)
                return info['machine'],info['paths']
            def remote(host): return dict(direction='upload',local='/source',remote=str(root/'real'),routes=[dict(host=host)],items=['file'])
            self.assertTrue(conflicts(resources(remote('user@one-ip'),resolver),resources(remote('different-user@another-ip'),resolver)))
            self.assertFalse(conflicts(resources(remote('one'),resolver),resources(dict(remote('two'),items=['different']),resolver)))

    def test_active_detached_session_reserves_target_even_after_restore(self):
        scheduler = TransferScheduler(lambda o: [Resource('remote:machine','/target',True)],lambda: None)
        active, pending = HeldJob(dict(direction='remote')), HeldJob(dict(direction='remote'))
        threads = []
        try:
            threads.append(scheduler.submit(active,attached=True)); self.assertTrue(active.entered.wait(2))
            threads.append(scheduler.submit(pending))
            self.wait_for(lambda: active.id in pending.queue_reason)
            self.assertFalse(pending.entered.is_set())
            active.release.set(); self.assertTrue(pending.entered.wait(2))
        finally:
            active.release.set();pending.release.set()
            for thread in threads:thread.join(3)
            scheduler.close()

    def test_queued_tasks_survive_restart_active_local_tasks_need_resume(self):
        with tempfile.TemporaryDirectory() as directory:
            queued = app.Job(dict(direction='copy',local='/source',destination='/target',items=['file']))
            active = app.Job(queued.options.copy());active.state='transferring'
            path=Path(directory)/'history.json'
            app.transfer_history.save(path,{queued.id:queued,active.id:active},app.LOCK)
            restored=app.transfer_history.restore(path,app.Job,app.RelayJob,app.RemoteJob)
            self.assertEqual(restored[queued.id].state,'queued')
            self.assertEqual(restored[active.id].state,'interrupted')


if __name__ == '__main__': unittest.main()
