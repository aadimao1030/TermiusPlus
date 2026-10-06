import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import shlex
import ctypes
import errno
from types import SimpleNamespace
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import app

class FileActionTests(unittest.TestCase):
    @staticmethod
    def unsupported_rename(error=errno.EINVAL):
        def rename(*args):
            ctypes.set_errno(error)
            return -1
        return SimpleNamespace(renameat2=rename,renamex_np=rename)

    def test_shared_filesystem_fallback_preserves_files_directories_and_links(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);(root/'file').write_text('keep')
            folder=root/'folder';folder.mkdir(mode=0o750);(folder/'nested').write_text('nested')
            (root/'link').symlink_to('file');(root/'dangling').symlink_to('missing')
            with patch('ctypes.CDLL',return_value=self.unsupported_rename()):
                for name in ['folder','link','dangling','file']:
                    app.file_operation(directory,str(root/name),'rename','new-'+name)
                    self.assertFalse((root/name).exists());self.assertFalse((root/name).is_symlink())
            self.assertEqual((root/'new-file').read_text(),'keep')
            self.assertEqual((root/'new-folder'/'nested').read_text(),'nested')
            self.assertEqual((root/'new-folder').stat().st_mode & 0o777,0o750)
            self.assertEqual((root/'new-link').readlink(),Path('file'))
            self.assertEqual((root/'new-dangling').readlink(),Path('missing'))

    def test_missing_libc_rename_function_uses_fallback(self):
        with tempfile.TemporaryDirectory() as directory:
            source=Path(directory)/'file';source.write_text('keep')
            with patch('ctypes.CDLL',return_value=SimpleNamespace()):
                result=app.file_operation(directory,str(source),'rename','new')
            self.assertEqual(Path(result['path']).read_text(),'keep')

    def test_fallback_destination_created_after_initial_check_is_not_overwritten(self):
        for kind in ['file','folder','link']:
            with self.subTest(kind=kind),tempfile.TemporaryDirectory() as directory:
                root=Path(directory);source=root/'source';target=root/'target'
                if kind=='folder': source.mkdir();target.mkdir()
                elif kind=='link': source.symlink_to('missing');target.symlink_to('other')
                else: source.write_text('source');target.write_text('target')
                real_lexists=app.os.path.lexists
                def stale_check(path): return False if str(path)==str(target) else real_lexists(path)
                with patch('ctypes.CDLL',return_value=self.unsupported_rename()),patch('os.path.lexists',side_effect=stale_check):
                    with self.assertRaises(FileExistsError): app.file_operation(directory,str(source),'rename','target')
                if kind=='file': self.assertEqual(target.read_text(),'target');self.assertEqual(source.read_text(),'source')
                if kind=='folder': self.assertTrue(source.is_dir());self.assertTrue(target.is_dir())
                if kind=='link': self.assertEqual(target.readlink(),Path('other'));self.assertTrue(source.is_symlink())

    def test_permission_errors_do_not_trigger_fallback(self):
        with tempfile.TemporaryDirectory() as directory:
            source=Path(directory)/'source';source.write_text('keep')
            with patch('ctypes.CDLL',return_value=self.unsupported_rename(errno.EACCES)),patch('os.link') as link,patch('os.mkdir') as mkdir:
                with self.assertRaises(PermissionError): app.file_operation(directory,str(source),'rename','new')
                link.assert_not_called();mkdir.assert_not_called()
            self.assertTrue(source.exists());self.assertFalse((source.parent/'new').exists())

    def test_failed_directory_fallback_removes_only_empty_reservation(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);source=root/'source';source.mkdir();(source/'file').write_text('keep')
            with patch('ctypes.CDLL',return_value=self.unsupported_rename()),patch('os.rename',side_effect=PermissionError(errno.EACCES,'denied')):
                with self.assertRaises(PermissionError): app.file_operation(directory,str(source),'rename','new')
            self.assertFalse((root/'new').exists());self.assertEqual((source/'file').read_text(),'keep')
            def populate_then_fail(*args):
                (root/'new'/'concurrent').write_text('keep concurrent data')
                raise OSError(errno.ENOTEMPTY,'not empty')
            with patch('ctypes.CDLL',return_value=self.unsupported_rename()),patch('os.rename',side_effect=populate_then_fail):
                with self.assertRaises(OSError): app.file_operation(directory,str(source),'rename','new')
            self.assertEqual((root/'new'/'concurrent').read_text(),'keep concurrent data')
            self.assertEqual((source/'file').read_text(),'keep')

    def test_create_in_current_and_nested_directories(self):
        with tempfile.TemporaryDirectory() as directory:
            folder=app.file_action(dict(side='local',root=directory,path=directory,operation='create_folder',name='资料 folder'))
            result=app.file_action(dict(side='local',root=directory,path=folder['path'],operation='create_file',name="中文 ' $(id).txt"))
            self.assertTrue(Path(folder['path']).is_dir())
            self.assertEqual(Path(result['path']).read_bytes(),b'')

    def test_create_never_overwrites_entries_or_symlinks(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);(root/'file').write_text('keep');(root/'folder').mkdir()
            (root/'link').symlink_to(root/'file');(root/'dangling').symlink_to(root/'missing')
            for operation in ['create_file','create_folder']:
                for name in ['file','folder','link','dangling']:
                    with self.assertRaisesRegex(ValueError,'同名'):
                        app.file_operation(directory,directory,operation,name)
            self.assertEqual((root/'file').read_text(),'keep')
            self.assertTrue((root/'dangling').is_symlink())
            self.assertFalse((root/'missing').exists())

    def test_create_rejects_invalid_names_and_locations(self):
        with tempfile.TemporaryDirectory() as directory,tempfile.TemporaryDirectory() as other:
            root=Path(directory);(root/'file').write_text('keep')
            for operation in ['create_file','create_folder']:
                for name in ['', '.', '..', '../escape', 'sub/name', 'bad\0name', None]:
                    with self.assertRaises(ValueError): app.file_operation(directory,directory,operation,name)
                for path in [other,str(root/'missing'),str(root/'file')]:
                    with self.assertRaises(ValueError): app.file_operation(directory,path,operation,'new')
            self.assertEqual(list(Path(other).iterdir()),[])

    def test_remote_api_creates_through_generated_ssh_command(self):
        # Run the actual generated remote Python command with an isolated transport.
        with tempfile.TemporaryDirectory() as directory:
            real_run = subprocess.run
            def transport(command, **kwargs):
                return real_run(shlex.split(command[-1]),**kwargs)
            with patch('app.ssh_args',return_value=['ssh']),patch('app.ssh_env',return_value=None),patch('app.subprocess.run',side_effect=transport):
                data=dict(side='remote',route=dict(host='fixture'),root=directory,path=directory,operation='create_folder',name='folder name')
                folder=app.file_action(data)
                data.update(path=folder['path'],operation='create_file',name="空白 ' $(id).txt")
                result=app.file_action(data)
            self.assertTrue(Path(folder['path']).is_dir())
            self.assertEqual(Path(result['path']).read_bytes(),b'')

    def test_rename_and_collision_preserve_both_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);source=root/'a';source.write_text('original');target=root/'b';target.write_text('keep')
            with self.assertRaises(ValueError): app.file_operation(directory,str(source),'rename','b')
            self.assertEqual(target.read_text(),'keep');self.assertEqual(source.read_text(),'original')
            result=app.file_operation(directory,str(source),'rename','中文 $(id).txt')
            self.assertEqual(Path(result['path']).read_text(),'original')

    def test_trash_symlink_does_not_touch_target(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);target=root/'folder';target.mkdir();(target/'file').write_text('keep');link=root/'link';link.symlink_to(target,target_is_directory=True)
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
            root=Path(directory);(root/'.termiusplus-trash').symlink_to(other,target_is_directory=True);file=root/'file';file.write_text('keep')
            with self.assertRaises(ValueError):app.file_operation(directory,str(file),'trash')
            self.assertTrue(file.exists())


class FileMenuTests(unittest.TestCase):
    def test_menu_targets_dialog_submission_and_expanded_tree_refresh(self):
        source=(Path(__file__).resolve().parents[1]/'web/scripts/files.js').read_text()
        helpers=source[source.index('let pendingFileAction = null;'):source.index('document.addEventListener("pointerdown", (event) => {',source.index('let pendingFileAction = null;'))]
        script=r'''
const assert = require('node:assert/strict');
const elements = new Map();
const $ = id => {
  if (!elements.has(id)) elements.set(id, {children:[],style:{},offsetWidth:180,offsetHeight:280,
    replaceChildren(){this.children=[]},append(button){this.children.push(button)},
    querySelector(){return this.children[0]},focus(){},select(){},showModal(){this.open=true},close(){this.open=false}});
  return elements.get(id);
};
const node=(_,text)=>({text,setAttribute(){},focus(){}});
const innerWidth=1000,innerHeight=800,sides=['local'];
const panes={local:{kind:'remote',root:'/root',loadedKey:'server',generation:0,connection:{host:'fixture'}}};
const endpointKey=()=> 'server';
const closeMenus=()=>{}; const focus=()=>{}; const select=()=>{};
const previewFile=()=>{}; const packSelection=()=>{};
const notice=()=>{}; const navigator={clipboard:{writeText:async()=>{}}};
const action=(_,callback)=>callback();
let request, opened=[];
const api=async(_,data)=>{request=data;return {path:data.path+'/'+data.name}};
const makeRow=path=>({dataset:{path},querySelector:()=>true,
  expandFolder:async()=>{opened.push(path);rows.push(makeRow(path+'/child'))}});
let rows=[makeRow('/root/folder'),makeRow('/root/folder/child')];
$('localTree').querySelectorAll=()=> rows;
$('localTree').scrollTop=80;
const load=async()=>{panes.local.generation++; rows=[makeRow('/root/folder')];$('localTree').scrollTop=0};
''' + helpers + r'''
(async()=>{
  const event={clientX:10,clientY:10};
  showFileMenu(event,'local',null);
  assert.deepEqual($('fileMenu').children.map(b=>b.text),['新建文件夹…','新建文件…','复制路径']);
  $('fileMenu').children[0].onclick();
  assert.equal($('fileActionTitle').textContent,'新建文件夹');
  assert.equal($('fileActionPath').textContent,'/root');
  assert.equal($('newFilename').value,'');
  assert.equal($('newFilename').required,true);
  assert.equal($('deleteNote').hidden,true);
  $('newFilename').value='new folder';
  await $('fileActionForm').onsubmit({preventDefault(){}});
  // The submit handler starts an asynchronous action; allow its refresh to finish.
  await new Promise(resolve=>setImmediate(resolve));
  assert.equal(request.path,'/root');
  assert.equal(request.operation,'create_folder');
  assert.equal(request.side,'remote');
  assert.equal(request.route.host,'fixture');
  assert.deepEqual(opened,['/root/folder','/root/folder/child']);
  assert.equal($('localTree').scrollTop,80);
  showFileMenu(event,'local',{directory:true,path:'/root/folder/child',name:'child'},()=>{});
  $('fileMenu').children[1].onclick();
  assert.equal($('fileActionPath').textContent,'/root/folder/child');
  assert.equal($('fileActionTitle').textContent,'新建文件');
  assert.equal($('newFilename').value,'');
  $('newFilename').value='empty.txt';
  await $('fileActionForm').onsubmit({preventDefault(){}});
  await new Promise(resolve=>setImmediate(resolve));
  assert.equal(request.path,'/root/folder/child');
  assert.equal(request.operation,'create_file');
  showFileMenu(event,'local',{directory:false,path:'/root/folder/a.txt',name:'a.txt'});
  $('fileMenu').children[0].onclick();
  assert.equal($('fileActionPath').textContent,'/root/folder');
  openFileAction('local',{path:'/root/a',name:'a'},'rename');
  assert.equal($('newFilename').value,'a');
  assert.equal($('confirmFileAction').textContent,'保存');
  openFileAction('local',{path:'/root/a',name:'a'},'trash');
  assert.equal($('renameField').hidden,true);
  assert.equal($('newFilename').required,false);
})().catch(error=>{console.error(error);process.exitCode=1});
'''
        result=subprocess.run(['node','-e',script],capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stderr)
