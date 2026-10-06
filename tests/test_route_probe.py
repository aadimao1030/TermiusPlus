import os
import shlex
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import app
from termiusplus import route_probe


class RouteProbeTests(unittest.TestCase):
    def test_upload_download_probe_uses_received_data_excludes_login_delay(self):
        with tempfile.TemporaryDirectory() as directory:
            adapter=Path(directory)/'ssh.py'
            adapter.write_text('import sys,time,shlex,subprocess\ntime.sleep(.4)\nsys.exit(subprocess.call(shlex.split(sys.argv[2])))\n')
            transport=lambda route:[sys.executable,str(adapter)]
            for direction in ['upload','download']:
                with self.subTest(direction=direction):
                    rate=route_probe.sample(dict(host='fixture'),threading.Event(),direction,transport,lambda r:None,app.stop_process,seconds=.5,limit=1024*1024)
                    self.assertGreater(rate,3*1024*1024,'login delay must not be counted as data transfer')

    def test_slow_upload_counts_receiver_acknowledgements_and_partial_probe(self):
        with tempfile.TemporaryDirectory() as directory:
            adapter=Path(directory)/'ssh.py'
            adapter.write_text("import os,time,json\nprint('READY',flush=True)\ntotal=0;started=time.monotonic()\nwhile True:\n block=os.read(0,4096)\n if not block:break\n time.sleep(.02);total+=len(block);print(json.dumps([total,time.monotonic()-started]),flush=True)\n")
            rate=route_probe.sample(dict(host='fixture'),None,'upload',lambda r:[sys.executable,str(adapter)],lambda r:None,app.stop_process,seconds=.6,limit=32*1024*1024)
            self.assertGreater(rate,100000)
            self.assertLess(rate,350000)

    def test_transient_burst_does_not_choose_route_and_invalid_direction_rejected(self):
        with patch.object(route_probe,'sample',side_effect=[20*1024*1024,2*1024*1024]):
            result=route_probe.measure({},None,'upload',None,None,None)
        self.assertEqual(result,2*1024*1024)
        with self.assertRaises(ValueError):route_probe.measure({},None,'invalid',None,None,None)

    def test_broken_short_download_is_not_reported_as_a_fast_route(self):
        with tempfile.TemporaryDirectory() as directory:
            adapter=Path(directory)/'ssh.py'
            adapter.write_text("import sys\nsys.stdout.buffer.write(b'READY\\n'+b'x'*131072);sys.stdout.buffer.flush();sys.exit(1)\n")
            with self.assertRaisesRegex(ValueError,'中断'):
                route_probe.sample(dict(host='fixture'),None,'download',lambda r:[sys.executable,str(adapter)],lambda r:None,app.stop_process,seconds=.5)

    def test_cancel_interrupts_a_probe_waiting_for_another_probe(self):
        cancel=threading.Event();cancel.set()
        route_probe.PROBE_LOCK.acquire()
        try:
            with self.assertRaisesRegex(ValueError,'取消'):
                route_probe.measure({},cancel,'download',None,None,None)
        finally:route_probe.PROBE_LOCK.release()

    def test_relative_degradation_triggers_recheck_above_absolute_threshold(self):
        samples=[(t,4*1024*1024) for t in range(80,101,4)]
        self.assertTrue(app.needs_route_check(samples,101,1024*1024,20*1024*1024,0,0))
        self.assertFalse(app.needs_route_check(samples,101,1024*1024,5*1024*1024,0,0))
        self.assertFalse(app.needs_route_check(samples,110,10*1024*1024,20*1024*1024,0,0))
        self.assertFalse(app.needs_route_check(samples,101,10*1024*1024,20*1024*1024,90,0))
        self.assertFalse(app.needs_route_check(samples,101,10*1024*1024,20*1024*1024,0,6))
        self.assertFalse(app.needs_route_check(samples,101,10*1024*1024,20*1024*1024,0,0,interval=120))

    def test_actual_recent_bytes_used_instead_of_cumulative_rsync_speed_or_resume_jump(self):
        job=app.Job(dict(direction='upload'))
        with patch('app.time.monotonic',side_effect=[0,1,2]):
            job.record_output('1,000,000,000 10% 999MB/s 0:00:01')
            job.record_output('1,001,048,576 10% 999MB/s 0:00:01')
            job.record_output('1,002,097,152 10% 999MB/s 0:00:01')
        self.assertEqual(list(job.byte_samples),[(1,1048576),(2,1048576)])

    def test_job_probes_in_transfer_direction_and_manual_route_skips_probes(self):
        with tempfile.TemporaryDirectory() as directory:
            for direction in ['upload','download']:
                job=app.Job(dict(direction=direction,local=directory,remote='/remote',routes=[dict(host='one'),dict(host='two')]))
                with patch('app.rsync_binary',return_value='rsync'),patch('app.remote_info',return_value=dict(identity='same',path='/remote')),patch('app.measure',return_value=10000) as measure,patch.object(job,'command',return_value=[sys.executable,'-c','pass']):
                    job.run()
                    self.assertEqual(job.state,'completed',list(job.log))
                    self.assertEqual([call.args[2] for call in measure.call_args_list],[direction,direction])
            job=app.Job(dict(direction='upload',auto=False,local=directory,remote='/remote',routes=[dict(host='one'),dict(host='two')]))
            with patch('app.rsync_binary',return_value='rsync'),patch('app.remote_info',return_value=dict(identity='same',path='/remote')),patch('app.measure') as measure,patch.object(job,'command',return_value=[sys.executable,'-c','pass']):
                job.run();measure.assert_not_called();self.assertEqual(job.route,'one')

    def test_reprobe_stops_data_stream_then_resumes_best_or_keeps_current(self):
        with tempfile.TemporaryDirectory() as directory:
            for alternative,expected,switches in [(5,'two',1),(1.1,'one',0)]:
                with self.subTest(alternative=alternative):
                    job=app.Job(dict(direction='upload',local=directory,remote='/remote',routes=[dict(host='one'),dict(host='two')]))
                    measurements=[];attempts=[]
                    def probe(route,*args):
                        if len(measurements)>=2:
                            self.assertIsNotNone(job.process.poll(),'probe must not compete with the data stream')
                        scores=[16,8,1,alternative]
                        value=scores[len(measurements)]*1024*1024
                        measurements.append(route['host'])
                        return value
                    def command(binary,route,*args):
                        attempts.append(route['host'])
                        return [sys.executable,'-c','import time;time.sleep(10)' if len(attempts)==1 else 'pass']
                    checks=[True]
                    def should_check(*args):return checks.pop() if checks else False
                    with patch('app.rsync_binary',return_value='rsync'),patch('app.remote_info',return_value=dict(identity='same',path='/remote')),patch('app.measure',side_effect=probe),patch('app.needs_route_check',side_effect=should_check),patch.object(job,'command',side_effect=command):
                        job.run()
                    self.assertEqual(job.state,'completed',list(job.log))
                    self.assertEqual(attempts,['one',expected])
                    self.assertEqual(job.switches,switches)


if __name__=='__main__':unittest.main()
