"""Same-machine drag moves, with preflight and source preservation on failure."""
import os
import shlex
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import app


class MoveTests(unittest.TestCase):
    def test_move_selection_preserves_contents_links_and_directory_structure(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); target = root / 'target'; target.mkdir()
            folder = root / '资料 folder'; folder.mkdir(); (folder / 'child').write_text('keep')
            file = root / "a ' $(id).txt"; file.write_text('file')
            link = root / 'link'; link.symlink_to('missing')
            result = app.file_action(dict(side='local', operation='move', root=directory, path=directory,
                paths=[str(folder), str(folder / 'child'), str(file), str(link)], destination=str(target)))
            self.assertEqual(len(result['moved']), 3)
            self.assertFalse(folder.exists()); self.assertFalse(file.exists()); self.assertFalse(link.is_symlink())
            self.assertEqual((target / folder.name / 'child').read_text(), 'keep')
            self.assertEqual((target / file.name).read_text(), 'file')
            self.assertEqual((target / 'link').readlink(), Path('missing'))

    def test_all_conflicts_checked_before_any_move(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); target = root / 'target'; target.mkdir()
            (root / 'a').write_text('a'); (root / 'b').write_text('b'); (target / 'b').write_text('existing')
            with self.assertRaisesRegex(ValueError, '同名'):
                app.move_entries(directory, [str(root / 'a'), str(root / 'b')], str(target))
            self.assertTrue((root / 'a').exists()); self.assertTrue((root / 'b').exists())
            self.assertFalse((target / 'a').exists()); self.assertEqual((target / 'b').read_text(), 'existing')

    def test_reject_duplicate_basenames_cycle_symlink_cycle_and_escape(self):
        with tempfile.TemporaryDirectory() as directory, tempfile.TemporaryDirectory() as other:
            root = Path(directory); folder = root / 'folder'; folder.mkdir(); child = folder / 'child'; child.mkdir()
            (root / 'same').write_text('one'); (folder / 'same').write_text('two')
            (root / 'alias').symlink_to(child, target_is_directory=True)
            for paths, destination in [
                ([str(root / 'same'), str(folder / 'same')], other),
                ([str(folder)], str(child)), ([str(folder)], str(root / 'alias')),
                ([str(root)], other), ([str(Path(other) / 'outside')], directory),
                ([str(root / 'same')], str(root / 'same')),
            ]:
                with self.subTest(paths=paths), self.assertRaises(ValueError):
                    app.move_entries(directory, paths, destination)
            self.assertEqual((root / 'same').read_text(), 'one')
            self.assertTrue(folder.exists()); self.assertEqual(list(Path(other).iterdir()), [])

    def test_move_between_separate_pane_roots_and_same_directory_noop(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); source = root / 'source'; target = root / 'target'
            source.mkdir(); target.mkdir(); file = source / 'file'; file.write_text('keep')
            result = app.move_entries(str(source), [str(file), str(file)], str(source))
            self.assertEqual(result['moved'], []); self.assertEqual(file.read_text(), 'keep')
            result = app.move_entries(str(source), [str(file)], str(target))
            self.assertEqual(result['moved'][0]['path'], str((target / 'file').resolve()))
            self.assertFalse(file.exists()); self.assertEqual((target / 'file').read_text(), 'keep')

    def test_partial_failure_returns_completed_items_and_keeps_remaining_sources(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); target = root / 'target'; target.mkdir()
            a = root / 'a'; b = root / 'b'; a.write_text('a'); b.write_text('b')
            real_operation = app.file_operation
            def fail_second(*args, **kwargs):
                if args[1] == str(b): raise PermissionError('denied')
                return real_operation(*args, **kwargs)
            with patch('termiusplus.filesystem.file_operation', side_effect=fail_second):
                result = app.move_entries(directory, [str(a), str(b)], str(target))
            self.assertEqual(len(result['moved']), 1); self.assertEqual(result['failed'], str(b))
            self.assertEqual(result['error'], 'denied'); self.assertEqual(b.read_text(), 'b')
            self.assertEqual((target / 'a').read_text(), 'a'); self.assertFalse(a.exists())

    def test_remote_api_runs_actual_move_script(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); file = root / 'file'; file.write_text('keep'); target = root / 'target'; target.mkdir()
            real_run = subprocess.run
            def transport(command, **kwargs): return real_run(shlex.split(command[-1]), **kwargs)
            with patch('app.ssh_args', return_value=['ssh']), patch('app.ssh_env', return_value=None), patch('app.subprocess.run', side_effect=transport):
                result = app.file_action(dict(side='remote', route=dict(host='fixture'), operation='move', root=directory,
                    path=directory, paths=[str(file)], destination=str(target)))
            self.assertEqual(len(result['moved']), 1); self.assertFalse(file.exists())
            self.assertEqual((target / 'file').read_text(), 'keep')

    def test_cross_filesystem_is_rejected_before_changing_sources(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); file = root / 'file'; file.write_text('keep'); target = root / 'target'; target.mkdir()
            real_stat = os.stat
            def other_device(path, **kwargs):
                result = real_stat(path, **kwargs)
                if os.path.realpath(path) == str(target.resolve()):
                    values = list(result); values[2] += 1
                    return os.stat_result(values)
                return result
            with patch('os.stat', side_effect=other_device):
                with self.assertRaisesRegex(ValueError, '跨文件系统'):
                    app.move_entries(directory, [str(file)], str(target))
            self.assertEqual(file.read_text(), 'keep'); self.assertEqual(list(target.iterdir()), [])


class DragMoveTests(unittest.TestCase):
    def test_same_machine_moves_other_machine_transfers_and_stale_drags_are_rejected(self):
        source = (ROOT / 'web/scripts/files.js').read_text()
        helper = source[source.index('function sameMachine('):source.index('async function stageExternal(')]
        harness = r'''
const assert = require('node:assert/strict');
const mime='items',sides=['local','remote'];
const panes={local:{kind:'remote',connection:{host:'server-a'},loadedKey:'a',root:'/source',machine:'same'},
remote:{kind:'remote',connection:{host:'server-b'},loadedKey:'b',root:'/destination',machine:'same'}};
const endpointKey=side=>side==='local'?'a':'b';
let moves=[],transfers=[],refreshes=[],notices=[],failure=null;
const api=async(_,options)=>{moves.push(options);return {moved:[{path:options.destination+'/file'}],error:failure}};
const refreshFileTree=async(side,path)=>refreshes.push([side,path]);
const notice=text=>notices.push(text);
const createTransfer=(...args)=>args;
const showTransfer=(...args)=>transfers.push(args);
const event=payload=>({dataTransfer:{getData:()=>JSON.stringify(payload)}});
const payload={side:'local',key:'a',root:'/source',paths:['/source/file']};
''' + helper + r'''
(async()=>{
  await drop(event(payload),'local','/source/folder');
  assert.equal(moves.length,1);assert.equal(moves[0].operation,'move');
  assert.deepEqual(moves[0].paths,['/source/file']);
  assert.equal(moves[0].destination,'/source/folder');assert.equal(transfers.length,0);
  assert.deepEqual(refreshes.map(x=>x[0]),['local','remote']);
  await drop(event(payload),'remote','/destination/folder');
  assert.equal(moves.length,2);assert.equal(moves[1].route.host,'server-a');
  assert.equal(moves[1].destination,'/destination/folder');
  panes.remote.machine='different';
  await drop(event(payload),'remote','/destination');
  assert.equal(moves.length,2);assert.equal(transfers.length,1);
  await assert.rejects(()=>drop(event({...payload,key:'old'}),'remote','/destination'),/源位置已变化/);
  panes.remote.loadedKey='old';
  await assert.rejects(()=>drop(event(payload),'remote','/destination'),/目标位置已变化/);
  panes.remote.loadedKey='b';panes.local.kind=panes.remote.kind='local';
  assert.equal(sameMachine('local','remote'),true);
  failure='permission denied';const initial=refreshes.length;
  await assert.rejects(()=>drop(event(payload),'remote','/destination'),/已移动 1 项/);
  assert.equal(refreshes.length,initial+2,'partial failure refreshes both panes');
})().catch(error=>{console.error(error);process.exitCode=1});
'''
        result = subprocess.run(['node', '-e', harness], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
