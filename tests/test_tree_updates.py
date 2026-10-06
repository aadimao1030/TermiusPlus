"""Range selection, recoverable batch deletion and in-place directory updates."""
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


class BatchTrashTests(unittest.TestCase):
    def test_selection_moves_to_trash_once_and_preserves_link_target(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); folder = root / 'folder'; folder.mkdir()
            file = folder / 'child'; file.write_text('keep')
            other = root / 'other'; other.write_text('other')
            link = root / 'link'; link.symlink_to(other)
            result = app.file_action(dict(side='local',root=directory,path=str(folder),operation='trash',
                paths=[str(folder),str(file),str(link),str(link)]))
            self.assertEqual(len(result['trashed']),2)
            self.assertFalse(folder.exists()); self.assertFalse(link.is_symlink())
            self.assertEqual(other.read_text(),'other')
            saved = {Path(item['original']).name:Path(item['path']) for item in result['trashed']}
            self.assertEqual((saved['folder']/'child').read_text(),'keep')
            self.assertTrue(saved['link'].is_symlink())

    def test_preflight_rejects_invalid_path_and_linked_trash_before_deleting(self):
        with tempfile.TemporaryDirectory() as directory, tempfile.TemporaryDirectory() as other:
            root=Path(directory); a=root/'a';a.write_text('keep');sub=root/'sub';sub.mkdir();b=sub/'b';b.write_text('b')
            for invalid in [directory,str(root/'missing'),str(Path(other)/'outside')]:
                with self.assertRaises(ValueError): app.trash_entries(directory,[str(a),invalid])
                self.assertTrue(a.exists())
            (sub/'.termiusplus-trash').symlink_to(other,target_is_directory=True)
            with self.assertRaises(ValueError): app.trash_entries(directory,[str(a),str(b)])
            self.assertTrue(a.exists());self.assertTrue(b.exists())

    def test_partial_failure_reports_exact_successes(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);a=root/'a';b=root/'b';a.write_text('a');b.write_text('b')
            real=app.file_operation
            def fail_second(*args,**kwargs):
                if args[1]==str(b):raise PermissionError('denied')
                return real(*args,**kwargs)
            with patch('termiusplus.filesystem.file_operation',side_effect=fail_second):
                result=app.trash_entries(directory,[str(a),str(b)])
            self.assertEqual(result['failed'],str(b));self.assertEqual(len(result['trashed']),1)
            self.assertFalse(a.exists());self.assertEqual(b.read_text(),'b')
            self.assertEqual(Path(result['trashed'][0]['path']).read_text(),'a')

    def test_remote_batch_script_preserves_all_contents(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);(root/'a').write_text('a');(root/'b').write_text('b')
            arguments=dict(root=directory,paths=[str(root/'a'),str(root/'b')])
            run=subprocess.run([sys.executable,'-c',app.REMOTE_FILE_TRASH_SCRIPT,json.dumps(arguments)],capture_output=True,check=True)
            result=json.loads(run.stdout)
            self.assertEqual([Path(item['path']).read_text() for item in result['trashed']],['a','b'])


class TreeUpdateTests(unittest.TestCase):
    def test_ranges_retained_nodes_expansion_scroll_selection_and_stale_refresh(self):
        source=(ROOT/'web/scripts/files.js').read_text()
        helpers=source[source.index('function relative(root, path)'):source.index('let pendingFileAction = null;')]
        refresh=source[source.index('async function refreshFileTree('):source.index('$("fileActionForm").onsubmit')]
        harness=r'''
const assert=require('node:assert/strict');
const elements=new Map();
class Element {
  constructor(cls='') {this.className=cls;this.children=[];this.dataset={};this.attrs={};this.hidden=false;this.scrollTop=0;}
  get classList(){return {contains:name=>this.className.split(' ').includes(name),
    add:name=>{if(!this.classList.contains(name))this.className+=' '+name},
    remove:name=>{this.className=this.className.split(' ').filter(n=>n!==name).join(' ')},
    toggle:(name,on)=>on?this.classList.add(name):this.classList.remove(name)}}
  get firstElementChild(){return this.children[0]||null}
  get lastElementChild(){return this.children.at(-1)||null}
  get nextElementSibling(){return this.parentElement?.children[this.parentElement.children.indexOf(this)+1]||null}
  get isConnected(){return this===tree||Boolean(this.parentElement?.isConnected)}
  append(...children){for(const child of children)this.insertBefore(child,null)}
  insertBefore(child,before){child.remove();const i=before?this.children.indexOf(before):this.children.length;this.children.splice(i,0,child);child.parentElement=this;}
  remove(){if(this.parentElement){const list=this.parentElement.children;list.splice(list.indexOf(this),1);this.parentElement=null}}
  replaceChildren(...children){for(const child of [...this.children])child.remove();this.append(...children)}
  setAttribute(k,v){this.attrs[k]=v}
  matches(selector){if(selector.startsWith('.'))return this.classList.contains(selector.slice(1));
    const match=selector.match(/^\[(.*?)="(.*?)"\]$/);return match&&this.attrs[match[1]]===match[2]}
  querySelectorAll(selector){return this.children.flatMap(child=>[...(child.matches(selector)?[child]:[]),...child.querySelectorAll(selector)])}
  querySelector(selector){return this.querySelectorAll(selector)[0]||null}
  getBoundingClientRect(){if(this===tree)return {top:0,bottom:120};const index=visibleFileRows('local').indexOf(this);return {top:index*24-tree.scrollTop,bottom:(index+1)*24-tree.scrollTop}}
}
const $=id=>{if(!elements.has(id))elements.set(id,new Element());return elements.get(id)};
const tree=$('localTree');tree.className='tree';
const document={querySelectorAll:selector=>{const [id,query]=selector.split(' ');return $(id.slice(1)).querySelectorAll(query)}};
const node=(tag,text,cls)=>{const element=new Element(cls);element.textContent=text;return element};
const icon=()=>new Element();const formatSize=size=>String(size);
const focus=()=>{};const notice=()=>{};const action=(_,callback)=>callback();
const cancelFolderClick=()=>{};const handleFolderClick=()=>{};
const load=()=>{throw Error('refresh must not navigate or rebuild the whole tree')};
const endpointKey=()=> 'endpoint';
const panes={local:{root:'/root',loadedKey:'endpoint',generation:0,selected:new Map(),showHidden:false,entries:[]}};
const fixture=new Map();let browse=async(_,path)=>({entries:fixture.get(path).map(entry=>({...entry}))});
const entry=(name,directory=false,size=1,parent='/root')=>({name,path:parent+'/'+name,directory,size});
const row=path=>tree.querySelectorAll('.entry').find(row=>row.dataset.path===path);
''' + helpers + refresh + r'''
(async()=>{
  const initial=[entry('.hidden'),entry('folder',true),entry('a'),entry('b'),entry('c'),entry('d'),entry('e')];
  fixture.set('/root',initial);fixture.set('/root/folder',[entry('child',false,1,'/root/folder')]);
  panes.local.entries=initial;drawEntries(tree,initial,'local');
  select('local',row('/root/a').entryData);
  select('local',row('/root/d').entryData,false,true);
  assert.deepEqual([...panes.local.selected.keys()],['/root/a','/root/b','/root/c','/root/d']);
  select('local',row('/root/c').entryData,false,true);
  assert.deepEqual([...panes.local.selected.keys()],['/root/a','/root/b','/root/c'],'range retains its original anchor');
  select('local',row('/root/e').entryData,true);
  assert.equal(panes.local.selected.size,4,'Ctrl/Cmd retains noncontiguous entries');
  select('local',row('/root/a').entryData);select('local',row('/root/folder').entryData,false,true);
  assert.equal(panes.local.selected.has('/root/.hidden'),false,'hidden entries excluded');
  const folder=row('/root/folder');await folder.expandFolder();
  const child=row('/root/folder/child');
  select('local',child.entryData);select('local',row('/root/a').entryData,false,true);
  assert.deepEqual([...panes.local.selected.keys()],['/root/folder/child','/root/a']);
  select('local',row('/root/c').entryData);
  const retained=row('/root/c');tree.scrollTop=96;const top=retained.getBoundingClientRect().top;
  fixture.set('/root',[entry('.hidden'),entry('folder',true),entry('b'),entry('c',false,42),entry('d'),entry('e'),entry('f')]);
  fixture.set('/root/folder',[entry('child',false,9,'/root/folder'),entry('new',false,1,'/root/folder')]);
  await refreshFileTree('local');
  assert.equal(row('/root/c'),retained,'unchanged rows must retain their DOM identity');
  assert.equal(row('/root/folder'),folder);assert.equal(row('/root/folder/child'),child);
  assert.equal(folder.querySelector('[aria-expanded="true"]')!==null,true);
  assert.equal(row('/root/a'),undefined);assert.ok(row('/root/f'));
  assert.equal(retained.querySelector('.entry-size').textContent,'42');
  assert.equal(child.querySelector('.entry-size').textContent,'9');
  assert.equal(panes.local.selected.get('/root/c').size,42);
  assert.equal(retained.getBoundingClientRect().top,top,'viewport anchored despite deletion above it');
  assert.equal(panes.local.generation,0,'refresh must not act as navigation');
  fixture.set('/root',fixture.get('/root').filter(item=>item.name!=='c'));
  await refreshFileTree('local');assert.equal(panes.local.selected.size,0,'only deleted selections removed');
  assert.equal(panes.local.selectionAnchor,null);
  // A root navigation while refresh is pending must discard that old response.
  let resolve;
  browse=()=>new Promise(done=>{resolve=done});
  const pending=refreshFileTree('local');panes.local.generation++;
  resolve({entries:[entry('stale')]});await pending;
  assert.equal(row('/root/stale'),undefined);
})().catch(error=>{console.error(error);process.exitCode=1});
'''
        result=subprocess.run(['node','-e',harness],capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stderr)
