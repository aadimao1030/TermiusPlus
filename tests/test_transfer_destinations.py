"""Selected tree entries must arrive directly in the chosen destination."""
import copy
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import app
from termiusplus import transfer_history


class TransferDestinationTests(unittest.TestCase):
    def test_fast_transfer_refreshes_target_even_if_first_status_is_completed(self):
        source = (ROOT / 'web/scripts/files.js').read_text()
        helper = source[source.index('async function startJob('):source.index('async function action(')]
        refresh = source[source.index('async function refresh()'):source.index('let layout = {}')]
        harness = r'''
const assert = require('node:assert/strict');
const lastJobStates = new Map(), sides = ['local', 'remote'];
const panes = {local:{kind:'local',root:'/source'},remote:{kind:'local',root:'/target'}};
const elements = {}; const $ = id => elements[id] ||= {value:'/target'};
let jobs = [{id:'old',state:'completed',direction:'copy',targetPane:'remote'}], next = 0;
let refreshing = false, terminalSupported, remoteTasksSupported, localHome, rsyncReady, staging = false;
const loadRemoteLinkDefaults = async () => {};
const renderJobs = () => {};
const notice = () => {};
const loads = [];
const load = async (side,path) => loads.push([side,path]);
const action = async (button, callback) => callback();
const api = async (path, options) => {
  if (path === 'status') return {jobs, home:'/home', rsync:{ok:true}};
  const job = {id:String(++next),state:'completed',direction:options.direction,targetPane:'remote'};
  jobs.unshift(job);
  return {id:job.id};
};
''' + helper + refresh + r'''
(async () => {
  await refresh();
  assert.equal(loads.length,0,'existing completed history must not reload panes');
  for (const path of ['start','resume','pack']) {
    await startJob(path,{direction:path === 'pack' ? 'pack' : 'copy'});
    await refresh();
    assert.deepEqual(loads.at(-1),['remote','/target']);
  }
  assert.equal(loads.length,3,'every newly completed task must refresh once');
  await refresh();
  assert.equal(loads.length,3,'unchanged history must not repeatedly reload');
})().catch(error => {console.error(error);process.exit(1)});
'''
        result = subprocess.run(['node', '-e', harness], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_frontend_selection_copies_nested_items_without_ancestors(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); source = root / 'source'; target = root / 'target'
            source.mkdir(); target.mkdir()
            (source / 'parent' / 'folder' / 'inside').mkdir(parents=True)
            (source / 'parent' / '报告.txt').write_text('file')
            (source / 'parent' / 'folder' / 'inside' / 'child').write_text('nested')
            script = (ROOT / 'web/scripts/files.js').read_text()
            helper = script[script.index('function createTransfer('):script.index('function showTransfer(')]
            harness = ('const panes={local:{kind:"local"},remote:{kind:"local"}};'
                       'const $=id=>({checked:true,value:1});'
                       'const endpointLabel=side=>side;'
                       'const relative=(root,path)=>path.slice(root.length+1);\n')
            arguments = json.dumps(['local', 'remote', str(source), str(target),
                                    [str(source / 'parent' / '报告.txt'), str(source / 'parent' / 'folder')]])
            result = subprocess.run(['node', '-e', harness + helper +
                                     '\nconsole.log(JSON.stringify(createTransfer(...' + arguments + ')));'],
                                    capture_output=True, text=True, check=True)
            job = app.Job(json.loads(result.stdout)); job.run()
            self.assertEqual(job.state, 'completed', list(job.log))
            self.assertEqual((target / '报告.txt').read_text(), 'file')
            self.assertEqual((target / 'folder' / 'inside' / 'child').read_text(), 'nested')
            self.assertFalse((target / 'parent').exists())
            self.assertEqual(job.snapshot()['target']['paths'],
                             [str(target / '报告.txt'), str(target / 'folder')])

    def test_upload_download_and_relay_arrive_at_target_root(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); source = root / 'source'; target = root / 'target'
            source.mkdir(); target.mkdir(); (source / 'parent').mkdir()
            payload = 'selected file'
            (source / 'parent' / 'file.txt').write_text(payload)
            adapter = root / 'transport.py'
            adapter.write_text('import sys,subprocess,shlex,os\nos.environ["PATH"]=' +
                               repr(str(Path(app.rsync_binary()).parent)) +
                               '+os.pathsep+os.environ["PATH"]\nargs=sys.argv[2:]\n'
                               'if len(args)==1:args=shlex.split(args[0])\n'
                               'sys.exit(subprocess.call(args))\n')
            route = dict(name='fixture', host='fixture')
            with patch('app.ssh_args', return_value=[sys.executable, str(adapter)]), patch('app.checkpoint'):
                for direction in ('upload', 'download', 'relay'):
                    with self.subTest(direction=direction):
                        if (target / 'file.txt').exists(): (target / 'file.txt').unlink()
                        options = dict(direction=direction, items=['parent/file.txt'], flattenItems=True)
                        if direction == 'relay':
                            options.update(source=dict(kind='remote', path=str(source), routes=[route]),
                                           destination=dict(kind='remote', path=str(target), routes=[route]))
                            job = app.RelayJob(options)
                        else:
                            options.update(routes=[route], local=str(source if direction == 'upload' else target),
                                           remote=str(target if direction == 'upload' else source))
                            job = app.Job(options)
                        job.run()
                        self.assertEqual(job.state, 'completed', list(job.log))
                        self.assertEqual((target / 'file.txt').read_text(), payload)
                        self.assertFalse((target / 'parent').exists())
                        self.assertEqual(job.snapshot()['target']['paths'], [str(target / 'file.txt')])

    def test_old_records_preserve_destination_and_resume_key(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); source = root / 'source'; target = root / 'target'
            (source / 'parent').mkdir(parents=True); target.mkdir()
            (source / 'parent' / 'file').write_text('legacy')
            options = dict(direction='copy', local=str(source), destination=str(target), items=['parent/file'])
            job = app.Job(copy.deepcopy(options)); job.run()
            self.assertEqual(job.state, 'completed', list(job.log))
            self.assertEqual((target / 'parent' / 'file').read_text(), 'legacy')
            self.assertEqual(job.snapshot()['target']['paths'], [str(target / 'parent' / 'file')])
            old_key = transfer_history.transfer_key(options)
            self.assertEqual(old_key, transfer_history.transfer_key(dict(options, flattenItems=False)))
            self.assertNotEqual(old_key, transfer_history.transfer_key(dict(options, flattenItems=True)))

    def test_same_name_selection_fails_before_writing(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); source = root / 'source'; target = root / 'target'
            (source / 'one').mkdir(parents=True); (source / 'two').mkdir(); target.mkdir()
            (source / 'one' / 'file').write_text('one'); (source / 'two' / 'file').write_text('two')
            job = app.Job(dict(direction='copy', local=str(source), destination=str(target),
                               items=['one/file', 'two/file'], flattenItems=True))
            job.run()
            self.assertEqual(job.state, 'failed')
            self.assertIn('同名', job.error)
            self.assertEqual(list(target.iterdir()), [])


if __name__ == '__main__': unittest.main()
