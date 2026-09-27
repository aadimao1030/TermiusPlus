import subprocess
import sys
import unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import app


class ProgressTests(unittest.TestCase):
    def test_actual_rsync_format_and_units(self):
        for unit,scale in [('k',1024),('M',1024**2),('G',1024**3),('',1)]:
            result=app.parse_progress('1,069,744,128   9%    6.16'+unit+'B/s    0:27:36')
            self.assertEqual(result['progress'],9)
            self.assertEqual(result['speed'],6.16*scale)
            self.assertEqual(result['bytes'],1069744128)
            self.assertEqual(result['eta'],'0:27:36')
        self.assertEqual(app.parse_progress('200  100% 5.80 MB/s 0:00:01 (xfr#1, to-chk=0/1)')['progress'],100)
        self.assertIsNone(app.parse_progress('error mentions 50% and 10MB/s'))

    def test_pipe_cr_split_and_last_record_without_newline(self):
        job=app.Job(dict(direction='upload'))
        text='1,010,335,744 8% 5.80MB/s 0:29:29\r1,069,744,128 9% 6.16MB/s 0:27:36'
        process=subprocess.Popen([sys.executable,'-c','import sys;sys.stdout.write('+repr(text)+');sys.stdout.flush()'],stdout=subprocess.PIPE)
        job.read_output(process);self.assertEqual(process.wait(),0)
        result=job.snapshot()
        self.assertEqual(result['stageProgress'],9)
        self.assertEqual(result['speed'],6.16*1024**2)
        self.assertEqual(result['transferredBytes'],1069744128)
        self.assertEqual(len(job.samples),2)
        self.assertEqual(len(job.log),2)

    def test_relay_stage_and_total_are_separate(self):
        relay=app.RelayJob(dict(direction='relay'))
        relay.state='transferring';relay.child=app.Job(dict(direction='download'))
        relay.child.state='transferring';relay.child.record_output('100 9% 6.16MB/s 0:27:36')
        result=relay.snapshot()
        self.assertEqual(result['stage'],'download')
        self.assertEqual(result['stageProgress'],9)
        self.assertEqual(result['progress'],4.5)
        self.assertEqual(result['speed'],6.16*1024**2)
        relay.phase=1
        result=relay.snapshot()
        self.assertEqual(result['stage'],'upload')
        self.assertEqual(result['progress'],54.5)
        relay.state='failed'
        self.assertEqual(relay.snapshot()['progress'],54.5)
        relay.state='completed'
        self.assertEqual(relay.snapshot()['progress'],100)

    def test_download_completed_does_not_complete_whole_relay(self):
        relay=app.RelayJob(dict(direction='relay'));relay.state='transferring'
        relay.child=app.Job(dict(direction='download'));relay.child.state='completed';relay.child.progress=100
        result=relay.snapshot()
        self.assertEqual(result['progress'],50)
        self.assertNotEqual(result['state'],'completed')

    def test_frontend_legacy_running_job_and_current_snapshot(self):
        html=(Path(__file__).resolve().parents[1]/'web/scripts/files.js').read_text()
        code=html[html.index('function transferMetrics('):html.index('const logViews ')]
        assertions="""
const assert=require('node:assert/strict');
const job={direction:'relay',state:'transferring',route:'下载 · jet',speed:0,progress:4.5,log:['01:17:20 下载: 1,069,744,128 9% 6.16MB/s 0:27:36']};
const m=transferMetrics(job);assert.equal(m.speed,6.16*1024**2);assert.equal(m.stageProgress,9);assert.equal(m.total,4.5);assert.match(transferMetricText(job,m),/下载阶段 9%.*总进度 4.5%/);
const next={...job,stage:'upload',stageProgress:20,progress:60,speed:3*1024**2};
assert.equal(transferMetrics(next).total,60);assert.equal(transferMetrics(next).speed,3*1024**2);
const done={...job,state:'completed',route:'jet → HPC',log:['上传: 1,757 100% 1.68MB/s 0:00:00']};assert.equal(transferMetrics(done).upload,true);assert.equal(transferMetrics(done).total,100);
const failed={...job,state:'failed',progress:0,route:'jet → HPC',log:['下载: 1,757 100% 1.68MB/s 0:00:00','上传: 无可用线路']};assert.equal(transferMetrics(failed).upload,true);assert.equal(transferMetrics(failed).stageProgress,0);assert.equal(transferMetrics(failed).total,50);
"""
        subprocess.run(['node','-e',code+assertions],capture_output=True,check=True)

if __name__=='__main__':unittest.main()
