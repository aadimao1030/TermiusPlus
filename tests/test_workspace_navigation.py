"""Switch between files, queue and a retained terminal without losing state."""
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class WorkspaceNavigationTests(unittest.TestCase):
    def test_three_views_keep_terminal_and_restore_file_scroll(self):
        source = (ROOT / 'web/scripts/files.js').read_text()
        navigation = source[source.index('let terminalFrame = null,'):
                            source.index('$("terminalNav").onclick')]
        harness = r'''
const assert = require('node:assert/strict');
const elements = new Map();
const make = () => ({hidden:false,dataset:{},style:{},
  classList:{values:new Set(),add(c){this.values.add(c)},remove(c){this.values.delete(c)},
    toggle(c,on){on?this.values.add(c):this.values.delete(c)},contains(c){return this.values.has(c)}},
  contentWindow:{messages:[],postMessage(m){this.messages.push(m)}},append(){}});
const $ = id => {if(!elements.has(id))elements.set(id,make());return elements.get(id)};
const listeners={};
const window={scrollY:340,scrollTo(x,y){this.scrollY=y},addEventListener(t,f){listeners[t]=f}};
const document={body:make(),createElement:make,querySelector:()=>make()};
const appOrigin='http://127.0.0.1:8765', token=()=> 'TEST', parent={};
const requestAnimationFrame=f=>f();
const panes={local:{kind:'local',root:'/tmp',selected:new Map()},remote:{kind:'remote'}};
const focused='local', localHome='/tmp', routes=[];
const withRoutePasswords=v=>v;
let latestJobs=[{id:'ongoing'}], renders=0, fetches=0, layouts=0;
const renderJobs=jobs=>{assert.equal(jobs,latestJobs);renders++};
const applyLayout=()=>layouts++, closeMenus=()=>{};
const fetch=async()=>{fetches++;return {ok:true,text:async()=>'<html>terminal</html>'}};
const notice=()=>{};
$('queueWorkspace').hidden=true;
'''
        checks = r'''
(async()=>{
 showQueue();
 assert.equal($('fileWorkspace').hidden,true);
 assert.equal($('queueWorkspace').hidden,false);
 assert.equal($('queueNav').classList.contains('active'),true);
 assert.equal($('fileTools').hidden,true);
 assert.equal(renders,1);
 await openTerminal();
 const retained=terminalFrame;
 assert.equal($('queueWorkspace').hidden,true);
 assert.equal(retained.hidden,false);
 showQueue();
 assert.equal(retained.hidden,true);
 await openTerminal();
 assert.equal(terminalFrame,retained);
 assert.equal(fetches,1);
 assert.equal(retained.contentWindow.messages.at(-1).type,'termiusplus:activate-terminal');
 showFiles();
 assert.equal(window.scrollY,340);
 assert.equal($('fileWorkspace').hidden,false);
 assert.equal($('queueWorkspace').hidden,true);
 assert.equal($('fileTools').hidden,false);
 assert.equal(layouts,1);
 // A queue request from the terminal iframe is accepted; other windows ignored.
 listeners.message({origin:appOrigin,source:{},data:{type:'termiusplus:show-queue'}});
 assert.equal($('queueWorkspace').hidden,true);
 listeners.message({origin:appOrigin,source:retained.contentWindow,data:{type:'termiusplus:show-queue'}});
 assert.equal($('queueWorkspace').hidden,false);
 showFiles();
 assert.equal(window.scrollY,340);
 // Files embedded in a standalone terminal also accept the host's queue tab.
 window.frameElement={dataset:{hostView:'terminal'}};
 listeners.message({origin:appOrigin,source:parent,data:{type:'termiusplus:show-queue'}});
 assert.equal($('queueWorkspace').hidden,false);
})().catch(e=>{console.error(e);process.exitCode=1});
'''
        result = subprocess.run(['node', '-e', harness + navigation + checks],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == '__main__':
    unittest.main()
