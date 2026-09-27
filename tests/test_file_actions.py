import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import app

class FileActionTests(unittest.TestCase):
    def test_rename_and_collision_preserve_both_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);source=root/'a';source.write_text('original');target=root/'b';target.write_text('keep')
            with self.assertRaises(ValueError): app.file_operation(directory,str(source),'rename','b')
            self.assertEqual(target.read_text(),'keep');self.assertEqual(source.read_text(),'original')
            result=app.file_operation(directory,str(source),'rename','中文 $(id).txt')
            self.assertEqual(Path(result['path']).read_text(),'original')

    def test_trash_symlink_does_not_touch_target(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);target=root/'folder';target.mkdir();(target/'file').write_text('keep');link=root/'link'
            try:
                link.symlink_to(target,target_is_directory=True)
            except OSError as exc:
                self.skipTest('symlinks unavailable: %s' % exc)
            result=app.file_operation(directory,str(link),'trash')
            self.assertFalse(link.is_symlink());self.assertTrue(Path(result['path']).is_symlink());self.assertEqual((target/'file').read_text(),'keep')

    def test_reject_escape_root_and_invalid_name(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);file=root/'file';file.write_text('keep')
            for path in [directory,str(root.parent/'outside')]:
                with self.assertRaises(ValueError):app.file_operation(directory,path,'trash')
            for name in ['','..','.','../escape','bad\0name']:
                with self.assertRaises(ValueError):app.file_operation(directory,str(file),'rename',name)
            self.assertEqual(file.read_text(),'keep')

    def test_remote_script_rename_directory_and_trash_roundtrip(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);folder=root/'folder';folder.mkdir();(folder/'file').write_text('keep')
            def run(path,operation,name=None):
                args=dict(root=directory,path=str(path),operation=operation,name=name)
                result=subprocess.run([sys.executable,'-c',app.REMOTE_FILE_OPERATION_SCRIPT,json.dumps(args)],capture_output=True,check=True)
                return json.loads(result.stdout)
            renamed=run(folder,'rename','renamed');trashed=run(renamed['path'],'trash')
            self.assertEqual((Path(trashed['path'])/'file').read_text(),'keep')

    def test_trash_rejects_linked_trash_directory(self):
        with tempfile.TemporaryDirectory() as directory,tempfile.TemporaryDirectory() as other:
            root=Path(directory)
            try:
                (root/'.termiusplus-trash').symlink_to(other,target_is_directory=True)
            except OSError as exc:
                self.skipTest('symlinks unavailable: %s' % exc)
            file=root/'file';file.write_text('keep')
            with self.assertRaises(ValueError):app.file_operation(directory,str(file),'trash')
            self.assertTrue(file.exists())
