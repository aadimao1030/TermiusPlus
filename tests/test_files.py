import json
import os
import subprocess
import sys
import tarfile
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import app
import platform_compat


class FilesTests(unittest.TestCase):
    def test_selection_rejects_escape_and_deduplicates_children(self):
        for value in ['../outside', '/absolute', 'folder/../outside', '.', 'a\0b', 'folder//file']:
            with self.assertRaises(ValueError):
                app.validate_items([value])
        self.assertEqual(app.validate_items(['folder/file', 'folder', 'one', 'one']), ['folder', 'one'])

    def test_selected_rsync_recurses_only_selected_items(self):
        binary = app.rsync_binary()
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); source = root/'src'; target = root/'dst'
            source.mkdir(); target.mkdir()
            (source/'folder name').mkdir()
            try:
                (source/'folder name'/'中文\nfile').write_text('nested')
            except OSError:
                self.skipTest('当前文件系统不接受文件名中的换行')
            (source/'-file name').write_text('file')
            (source/'unselected').write_text('leave')
            (target/'preserve').write_text('keep')
            job = app.Job({'direction': 'upload'})
            job.manifest = app.create_manifest(['folder name', '-file name'])
            try:
                command = job.command(binary, {'host': 'test'}, '/unused', str(source))
                command[-1] = platform_compat.directory_argument(str(target), platform_compat.rsync_path_style(binary))
                subprocess.run(command, capture_output=True, check=True, env=platform_compat.rsync_env())
                self.assertEqual((target/'folder name'/'中文\nfile').read_text(), 'nested')
                self.assertEqual((target/'-file name').read_text(), 'file')
                self.assertFalse((target/'unselected').exists())
                self.assertEqual((target/'preserve').read_text(), 'keep')
            finally:
                Path(job.manifest).unlink()

    def test_archive_roundtrip_preserves_names_empty_dirs_and_links(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root/'资料 folder').mkdir(); (root/'资料 folder'/'empty').mkdir()
            (root/'资料 folder'/'a $(id).txt').write_text('hello')
            try:
                (root/'link').symlink_to('资料 folder')
            except OSError as exc:
                self.skipTest('symlinks unavailable: %s' % exc)
            result = app.local_archive(root, ['资料 folder', 'link'])
            with tarfile.open(result) as archive:
                self.assertIn('资料 folder/empty', archive.getnames())
                self.assertEqual(archive.extractfile('资料 folder/a $(id).txt').read(), b'hello')
                self.assertTrue(archive.getmember('link').issym())
            self.assertTrue((root/'资料 folder'/'a $(id).txt').exists())
            self.assertFalse(any(e.name.endswith('.part') for e in root.iterdir()))

    def test_archive_cancel_cleans_partial(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); (root/'file').write_bytes(os.urandom(50000))
            cancelled = threading.Event(); cancelled.set()
            with self.assertRaises(ValueError):
                app.local_archive(root, ['file'], cancelled)
            self.assertEqual([p.name for p in root.iterdir()], ['file'])

    def test_archive_cancellation_during_single_file(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); (root/'file').write_bytes(os.urandom(50000))
            cancelled = threading.Event()
            original = tarfile.TarFile.addfile
            def interrupt(archive, member, stream=None):
                if stream is not None:
                    cancelled.set()
                return original(archive, member, stream)
            with patch.object(tarfile.TarFile, 'addfile', interrupt):
                with self.assertRaisesRegex(ValueError, '取消'):
                    app.local_archive(root, ['file'], cancelled)
            self.assertEqual([p.name for p in root.iterdir()], ['file'])

    def test_archive_rejects_symlinked_parent_escape(self):
        with tempfile.TemporaryDirectory() as d, tempfile.TemporaryDirectory() as other:
            root = Path(d); (Path(other)/'secret').write_text('secret')
            try:
                (root/'link').symlink_to(other, target_is_directory=True)
            except OSError as exc:
                self.skipTest('symlinks unavailable: %s' % exc)
            with self.assertRaises(ValueError):
                app.local_archive(root, ['link/secret'])

    def test_remote_archive_script_roundtrip_and_no_overwrite(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); (root/"a' $(id).txt").write_text('safe')
            args = [sys.executable, '-c', app.REMOTE_ARCHIVE_SCRIPT, d, json.dumps(["a' $(id).txt"]), 'bundle.tar.gz']
            first = subprocess.run(args, capture_output=True, check=True)
            archive_path = json.loads(first.stdout)['path']
            before = Path(archive_path).read_bytes()
            again = subprocess.run(args, capture_output=True)
            self.assertNotEqual(again.returncode, 0)
            self.assertEqual(Path(archive_path).read_bytes(), before)
            self.assertFalse((root/'.bundle.tar.gz.part').exists())

    def test_staging_multiple_chunks_and_empty_directory(self):
        identifier = app.stage_create()
        try:
            app.stage_write(identifier, 'dir/empty', 0, b'', directory=True)
            app.stage_write(identifier, 'dir/file', 0, b'abc')
            app.stage_write(identifier, 'dir/file', 3, b'def')
            app.stage_write(identifier, 'zero', 0, b'')
            sealed = app.stage_seal(identifier)
            self.assertEqual((Path(sealed['path'])/'dir/file').read_bytes(), b'abcdef')
            self.assertTrue((Path(sealed['path'])/'dir/empty').is_dir())
            self.assertTrue((Path(sealed['path'])/'zero').is_file())
            with self.assertRaises(ValueError):
                app.stage_write(identifier, 'new', 0, b'no')
        finally:
            app.stage_release(identifier)
        self.assertNotIn(identifier, app.STAGES)

    def test_staging_rejects_traversal_and_wrong_offsets(self):
        identifier = app.stage_create()
        try:
            with self.assertRaises(ValueError):
                app.stage_write(identifier, '../escape', 0, b'bad')
            app.stage_write(identifier, 'file', 0, b'abc')
            with self.assertRaises(ValueError):
                app.stage_write(identifier, 'file', 0, b'duplicate')
            with self.assertRaises(ValueError):
                app.stage_write(identifier, 'file', 9, b'gap')
        finally:
            app.stage_release(identifier)

    def test_pack_then_transfer_both_directions_over_local_transport(self):
        # Execute the actual rsync remote-shell protocol against an isolated local
        # process; this verifies the full pipeline without configuring an SSH server.
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); local = root/'local'; remote = root/'remote'
            local.mkdir(); remote.mkdir()
            (local/'folder').mkdir(); (local/'folder'/'中文.txt').write_text('upload')
            adapter = root/'transport.py'
            adapter.write_text("import sys,subprocess,shlex,os\nos.environ['PATH']=" + repr(str(Path(app.rsync_binary()).parent)) + "+os.pathsep+os.environ['PATH']\nargs=sys.argv[2:]\nif len(args)==1: args=shlex.split(args[0])\nsys.exit(subprocess.call(args))\n")
            with patch('app.ssh_args', return_value=[sys.executable, str(adapter)]):
                upload = app.Job({'direction': 'upload', 'routes': [{'host': 'fixture'}], 'local': str(local), 'remote': str(remote), 'items': ['folder'], 'pack': True})
                upload.run()
                self.assertEqual(upload.state, 'completed', list(upload.log))
                uploaded = remote / Path(upload.result['archive']).name
                with tarfile.open(uploaded) as archive:
                    self.assertEqual(archive.extractfile('folder/中文.txt').read(), b'upload')
                (remote/'remote.txt').write_text('download')
                download = app.Job({'direction': 'download', 'routes': [{'host': 'fixture'}], 'local': str(local), 'remote': str(remote), 'items': ['remote.txt'], 'pack': True})
                download.run()
                self.assertEqual(download.state, 'completed', list(download.log))
                received = local / Path(download.result['archive']).name
                with tarfile.open(received) as archive:
                    self.assertEqual(archive.extractfile('remote.txt').read(), b'download')

    def test_local_copy_selection_and_nested_target_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); source = root/'source'; target = root/'target'
            source.mkdir(); target.mkdir(); (source/'folder').mkdir()
            (source/'folder'/'file').write_text('copy'); (source/'other').write_text('skip')
            job = app.Job(dict(direction='copy', local=str(source), destination=str(target), items=['folder'], sourcePane='remote', targetPane='local'))
            job.run()
            self.assertEqual(job.state, 'completed', list(job.log))
            self.assertEqual((target/'folder'/'file').read_text(), 'copy')
            self.assertFalse((target/'other').exists())
            self.assertEqual(job.snapshot()['targetPane'], 'local')
            invalid = app.Job(dict(direction='copy', local=str(source), destination=str(source/'folder')))
            invalid.run()
            self.assertEqual(invalid.state, 'failed')

    def test_remote_relay_selected_files_and_pack(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); source = root/'source'; target = root/'target'
            source.mkdir(); target.mkdir(); (source/'folder').mkdir()
            (source/'folder'/'file').write_text('relay'); (source/'other').write_text('skip')
            adapter = root/'transport.py'
            adapter.write_text("import sys,subprocess,shlex,os\nos.environ['PATH']=" + repr(str(Path(app.rsync_binary()).parent)) + "+os.pathsep+os.environ['PATH']\nargs=sys.argv[2:]\nif len(args)==1: args=shlex.split(args[0])\nsys.exit(subprocess.call(args))\n")
            endpoints = dict(source=dict(kind='remote', path=str(source), routes=[{'host':'fixture'}]), destination=dict(kind='remote', path=str(target), routes=[{'host':'fixture'}]))
            with patch('app.ssh_args', return_value=[sys.executable, str(adapter)]):
                for packed in (False, True):
                    job = app.RelayJob(dict(direction='relay', items=['folder'], pack=packed, **endpoints))
                    job.run()
                    self.assertEqual(job.state, 'completed', list(job.log))
                    self.assertEqual(job.snapshot()['progress'], 100)
                self.assertEqual((target/'folder'/'file').read_text(), 'relay')
                self.assertFalse((target/'other').exists())
                archives = list(target.glob('*.tar.gz'))
                self.assertEqual(len(archives), 1)
                with tarfile.open(archives[0]) as archive:
                    self.assertEqual(archive.extractfile('folder/file').read(), b'relay')

    def test_pack_job_returns_real_archive(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d)/'file').write_text('sample')
            job = app.Job({'direction': 'pack', 'side': 'local', 'local': d, 'items': ['file']})
            job.run()
            self.assertEqual(job.state, 'completed')
            self.assertTrue(Path(job.result['archive']).exists())
            self.assertEqual(job.snapshot()['side'], 'local')

if __name__ == '__main__':
    unittest.main()
