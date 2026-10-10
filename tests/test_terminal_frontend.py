import json
import subprocess
import sys
import unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))

ROOT=Path(__file__).resolve().parents[1]

# A minimal DOM/xterm stand-in so terminal.html's real script can run under node.
DOM=r"""
const assert=require('node:assert/strict');
function mkEl(tag){const el={tagName:(tag||'div').toUpperCase(),children:[],attributes:{},dataset:{},style:{},
 hidden:false,disabled:false,className:'',textContent:'',tabIndex:0,clientWidth:0,clientHeight:0,href:'',title:'',
 classList:{_s:new Set(),add(...c){c.forEach(x=>this._s.add(x))},remove(...c){c.forEach(x=>this._s.delete(x))},
  toggle(c,on){on?this._s.add(c):this._s.delete(c)},contains(c){return this._s.has(c)}},
 contentWindow:{messages:[],postMessage(m){this.messages.push(m)}},
 append(...n){n.forEach(x=>this.children.push(x))},replaceChildren(...n){this.children=n},
 setAttribute(k,v){this.attributes[k]=String(v)},getAttribute(k){return this.attributes[k]},
 remove(){},focus(){},contains(n){return n===this||this.children.includes(n)},
 getBoundingClientRect(){return {left:10,top:0,bottom:30,right:120}},
 querySelector(){return null},querySelectorAll(){return []},addEventListener(){}};return el}
const stubIds={};for(const id of ['newSession','sessionPicker','pickerRoutes','sessionTabs','terminal','emptyTerminal','state','filesLink','queueLink','terminalSelf'])stubIds[id]=mkEl('div');
const document={baseURI:'http://127.0.0.1:19387/',title:'',body:mkEl('body'),
 getElementById:id=>stubIds[id]||null,createElement:mkEl,createTextNode:t=>({text:t}),addEventListener(){}};
const listeners={};
const hostMessages=[];
const parentStub={postMessage(message){hostMessages.push(message)}};
const parent=parentStub;
const window={frameElement:{dataset:{fragment:'TOKEN?config='+encodeURIComponent(__CONFIG__),hostView:'files'}},innerWidth:1200,innerHeight:800,scrollY:0,
 addEventListener(t,f){(listeners[t]=listeners[t]||[]).push(f)},scrollTo(){}};
const location={hash:''};
const localStorage={getItem:()=>null,setItem(){}};
const navigator={clipboard:{writeText:()=>Promise.resolve()}};
const opened=[],closed=[];
const fetch=async(url,options)=>{
 if(!options)return {ok:true,text:async()=>'<html>files</html>'};
 const payload=JSON.parse(options.body);
 if(String(url).endsWith('/close'))closed.push(payload);
 if(String(url).endsWith('/open')){opened.push(payload);
  return {ok:true,json:async()=>({id:'sess'+(opened.length),name:payload.local?'本地':'远程',local:!!payload.local})}}
 if(String(url).endsWith('/poll'))return {ok:true,json:async()=>({content:'',cursor:0,truncated:false,done:false,exitCode:null})};
 return {ok:true,json:async()=>({ok:true})}};
function StubTerminal(){this.cols=80;this.rows=24;this.textarea=mkEl('textarea');
 this.onData=f=>{this.dataHandler=f};this.onBinary=f=>{this.binaryHandler=f};this.onResize=f=>{this.resizeHandler=f};
 this.attachCustomKeyEventHandler=()=>{};this.loadAddon=()=>{};this.open=()=>{};this.write=(d,cb)=>cb&&cb();
 this.reset=()=>{};this.writeln=()=>{};this.focus=()=>{};this.getSelection=()=>'';this.dispose=()=>{}}
const Terminal=StubTerminal;
const FitAddon={FitAddon:function(){this.fit=()=>{}}};
const ResizeObserver=function(){this.observe=()=>{}};
const requestAnimationFrame=cb=>{cb();return 0};
"""

ASSERTIONS=r"""
const tick=()=>new Promise(r=>setTimeout(r,10));
const hpc={name:'hpc',host:'hpc.example',port:22,jump:'',key:''};
(async()=>{
 await tick();
 // The local pane's terminal button autoconnects a local shell in that directory.
 assert.equal(opened.length,1);
 assert.equal(opened[0].local,true);
 assert.equal(opened[0].path,'/tmp/pane dir');
 assert.ok(!('route' in opened[0]));
 // The ＋ picker always offers 本地 first, then the SSH routes.
 assert.equal(stubIds.pickerRoutes.children.length,2);
 assert.equal(stubIds.pickerRoutes.children[0].children[0].text,'本地 · 这台电脑');
 assert.equal(stubIds.pickerRoutes.children[1].children[0].text,'jet');
 stubIds.pickerRoutes.children[0].onclick();
 await tick();
 assert.equal(opened.length,2);
 assert.equal(opened[1].local,true);
 assert.equal(opened[1].path,'/tmp/local dir');
 stubIds.pickerRoutes.children[1].onclick();
 await tick();
 assert.equal(opened.length,3);
 assert.equal(opened[2].local,false);
 assert.deepEqual(opened[2].route,{name:'jet',host:'jet.example',port:22,jump:'',key:''});
 assert.equal(stubIds.sessionTabs.children.length,3);
 const message=listeners['message'][0];
 const host=data=>message({origin:'http://127.0.0.1:19387',source:parent,data});
 // An already-open local terminal is activated instead of duplicated.
 host({type:'termiusplus:show-terminal',requestConnection:true,config:{local:true,path:'/tmp/other',routes}});
 host({type:'termiusplus:show-terminal',requestConnection:true,config:{local:true,path:'/tmp/other',routes}});
 await tick();
 assert.equal(opened.length,3);
 assert.equal(stubIds.sessionTabs.children.length,3);
 // Route updates refresh the picker.
 host({type:'termiusplus:update-routes',config:{routes:[routes[0],hpc]}});
 assert.equal(stubIds.pickerRoutes.children.length,3);
 assert.equal(stubIds.pickerRoutes.children[2].children[0].text,'hpc');
 // A host request for a connection that is not open yet starts a session.
 host({type:'termiusplus:show-terminal',requestConnection:true,config:{route:hpc,path:'/tmp/remote',routes:[routes[0],hpc]}});
 await tick();
 assert.equal(opened.length,4);
 assert.deepEqual(opened[3].route,hpc);
 assert.equal(opened[3].path,'/tmp/remote');
 assert.equal(opened[3].local,false);
 // Messages from another origin are ignored.
 message({origin:'https://evil.example',source:parent,data:{type:'termiusplus:show-terminal',requestConnection:true,config:{local:true,path:'/tmp/evil',routes}}});
 await tick();
 assert.equal(opened.length,4);
 // The local option survives having no SSH routes at all.
 host({type:'termiusplus:update-routes',config:{routes:[]}});
 assert.equal(stubIds.pickerRoutes.children.length,2);
 assert.equal(stubIds.pickerRoutes.children[0].children[0].text,'本地 · 这台电脑');
 assert.match(stubIds.pickerRoutes.children[1].textContent,/本地终端/);
 console.log('FRONTEND_OK opened='+opened.length);
 process.exit(0)})().catch(e=>{console.error(e);process.exit(1)});
"""

PICKER_ONLY_ASSERTIONS=r"""
const tick=()=>new Promise(r=>setTimeout(r,10));
(async()=>{
 await tick();
 // No pane offers a definite target: nothing connects by itself.
 assert.equal(opened.length,0);
 assert.equal(stubIds.pickerRoutes.children.length,2);
 assert.equal(stubIds.pickerRoutes.children[0].children[0].text,'本地 · 这台电脑');
 stubIds.pickerRoutes.children[0].onclick();
 await tick();
 assert.equal(opened.length,1);
 assert.equal(opened[0].local,true);
 assert.equal(opened[0].path,'/tmp/local dir');
 console.log('FRONTEND_OK opened='+opened.length);
 process.exit(0)})().catch(e=>{console.error(e);process.exit(1)});
"""

JET={'name':'jet','host':'jet.example','port':22,'jump':'','key':''}


class TerminalFrontendTests(unittest.TestCase):
    def run_frontend(self, config, assertions):
        frontend=(ROOT/'web/scripts/terminal.js').read_text()
        config=dict(config,routes=[dict(JET)],filesUrl='http://127.0.0.1:19387/#TOKEN')
        code=DOM.replace('__CONFIG__',json.dumps(json.dumps(config)))+frontend+assertions
        result=subprocess.run(['node','-e',code],capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertIn('FRONTEND_OK',result.stdout)

    def test_local_option_in_picker_and_pane_terminal_button(self):
        self.run_frontend(dict(local=True,route=None,path='/tmp/pane dir',localPath='/tmp/local dir',autoConnect=True),ASSERTIONS)

    def test_picker_is_shown_when_the_focused_pane_has_no_connection(self):
        self.run_frontend(dict(local=False,route=None,path='',localPath='/tmp/local dir',autoConnect=False),PICKER_ONLY_ASSERTIONS)

    def test_queue_navigation_preserves_terminal_sessions_and_files_frame(self):
        self.run_frontend(dict(local=True,path='/tmp',autoConnect=True),r"""
(async()=>{
 await new Promise(r=>setTimeout(r,10));
 const id=sessions[0].id;
 await stubIds.queueLink.onclick();
 assert.equal(hostMessages.at(-1).type,'termiusplus:show-queue');
 assert.equal(sessions[0].id,id);
 assert.equal(closed.length,0);
 // A standalone /terminal page reuses one files iframe for both file views.
 window.frameElement=null;
 await showFileWorkspace('queue');
 const frame=filesFrame;
 assert.equal(frame.dataset.view,'queue');
 assert.equal(document.body.children.filter(e=>e.tagName==='IFRAME').length,1);
 stubIds.terminalSelf.onclick();
 assert.equal(frame.hidden,true);
 await showFileWorkspace('files');
 assert.equal(filesFrame,frame);
 assert.equal(frame.contentWindow.messages.at(-1).type,'termiusplus:show-files');
 await showFileWorkspace('queue');
 assert.equal(frame.contentWindow.messages.at(-1).type,'termiusplus:show-queue');
 assert.equal(sessions[0].id,id);
 assert.equal(opened.length,1);
 assert.equal(closed.length,0);
 console.log('FRONTEND_OK');process.exit(0);
})().catch(e=>{console.error(e);process.exit(1)});
""")

    def test_cached_pagehide_preserves_session_and_true_unload_closes_it(self):
        self.run_frontend(dict(local=True,path='/tmp',autoConnect=True),r"""
(async()=>{
 await new Promise(r=>setTimeout(r,10));
 const s=sessions[0],id=s.id;
 assert.ok(id);
 listeners.pagehide[0]({persisted:true});
 assert.equal(s.id,id);assert.equal(s.closed,false);assert.equal(closed.length,0);
 listeners.pagehide[0]({persisted:false});
 assert.equal(s.closed,true);assert.deepEqual(closed,[{id}]);
 console.log('FRONTEND_OK');process.exit(0);
})().catch(e=>{console.error(e);process.exit(1)});
""")

    def test_poll_recovers_after_many_failures_without_closing_or_losing_output(self):
        frontend=(ROOT/'web/scripts/terminal.js').read_text()
        polling=frontend[frontend.index('async function poll('):frontend.index('function send(')]
        code=r"""
const assert=require('node:assert/strict');
const waits=[],requests=[],output=[],states=[];
async function delay(ms){waits.push(ms)}
function sessionState(s,text){states.push(text)}
const s={id:'original',generation:3,closed:false,cursor:42,name:'fixture',
 term:{write(data,cb){output.push(Buffer.from(data).toString());cb()},writeln(){}}};
async function api(operation,data){
 assert.equal(operation,'poll');requests.push({...data});
 if(requests.length<=7)throw Error('temporary network failure');
 if(requests.length===8)return {content:Buffer.from('recovered').toString('base64'),cursor:51,done:false};
 return {content:'',cursor:51,done:true,exitCode:0};
}
__POLL__
(async()=>{
 await poll(s,'original',3);
 assert.equal(requests.length,9);
 requests.slice(0,8).forEach(r=>assert.deepEqual(r,{id:'original',cursor:42}));
 assert.deepEqual(requests[8],{id:'original',cursor:51});
 assert.deepEqual(output,['recovered']);assert.equal(s.closed,false);
 assert.ok(waits.every(ms=>ms<=10000));assert.equal(s.cursor,51);
 // A replaced server/token cannot restore this ID, but must not send a close RPC.
 requests.length=0;s.id='original';s.generation=4;
 api=async(operation)=>{assert.equal(operation,'poll');throw Object.assign(Error('unauthorized'),{status:401})};
 await poll(s,'original',4);assert.equal(s.id,null);assert.equal(s.closed,false);
 console.log('FRONTEND_OK');
})().catch(e=>{console.error(e);process.exit(1)});
""".replace('__POLL__',polling)
        result=subprocess.run(['node','-e',code],capture_output=True,text=True,timeout=10)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertIn('FRONTEND_OK',result.stdout)

    def test_stuck_poll_request_is_aborted_so_recovery_can_continue(self):
        frontend=(ROOT/'web/scripts/terminal.js').read_text()
        api=frontend[frontend.index('async function api('):frontend.index('function delay(')]
        code=r"""
const assert=require('node:assert/strict'),token='fixture';
let timeoutCallback,cleared=false;
function setTimeout(callback,ms){assert.equal(ms,20000);timeoutCallback=callback;return 7}
function clearTimeout(id){assert.equal(id,7);cleared=true}
const fetch=(url,options)=>new Promise((resolve,reject)=>{
 assert.ok(options.signal);options.signal.addEventListener('abort',()=>reject(Error('aborted')));
});
__API__
(async()=>{
 const request=api('poll',{id:'same-session',cursor:42});
 timeoutCallback();await assert.rejects(request,/aborted/);assert.equal(cleared,true);
 console.log('FRONTEND_OK');
})().catch(e=>{console.error(e);process.exit(1)});
""".replace('__API__',api)
        result=subprocess.run(['node','-e',code],capture_output=True,text=True,timeout=10)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertIn('FRONTEND_OK',result.stdout)

    def test_file_pane_terminal_buttons_map_to_local_or_the_pane_connection(self):
        html=(ROOT/'web/scripts/files.js').read_text()
        helpers=html[html.index('const routePasswords = new Map();'):html.index('let routes = [],')]
        head=html[html.index('function terminalConfig('):html.index('async function openTerminal(')]
        jet={'name':'jet','host':'jet.example','port':22,'jump':'','key':''}
        code=r"""
const assert=require('node:assert/strict');
const routes=[__JET__],jet=routes[0],localHome='/Users/me',appOrigin='http://127.0.0.1:19387';
function closeMenus(){}
function token(){return 'TOKEN'}
__HELPERS__
__HEAD__
function build(side,panes){return terminalConfig(side,panes)}
const connected={kind:'remote',root:'/srv/app',connection:routes[0]};
assert.deepEqual(build('local',{local:{kind:'local',root:'/Users/me/Project'},remote:connected}),
 {route:undefined,local:true,routes,path:'/Users/me/Project',localPath:'/Users/me/Project',filesUrl:'http://127.0.0.1:19387/#TOKEN',autoConnect:true});
assert.deepEqual(build('remote',{local:{kind:'local',root:'/Users/me/Project'},remote:connected}),
 {route:jet,local:false,routes,path:'/srv/app',localPath:'/Users/me/Project',filesUrl:'http://127.0.0.1:19387/#TOKEN',autoConnect:true});
assert.deepEqual(build('remote',{local:{kind:'local',root:''},remote:{kind:'remote',root:'',connection:null}}),
 {route:undefined,local:false,routes,path:'',localPath:'/Users/me',filesUrl:'http://127.0.0.1:19387/#TOKEN',autoConnect:false});
assert.deepEqual(build('local',{local:{kind:'local',root:''},remote:{kind:'remote',root:'',connection:null}}).path,'/Users/me');
assert.equal(build('remote',{local:connected,remote:{kind:'local',root:'/tmp/right-local'}}).localPath,'/tmp/right-local');
console.log('FRONTEND_OK');
""".replace('__HELPERS__',helpers).replace('__HEAD__',head).replace('__JET__',json.dumps(jet))
        result=subprocess.run(['node','-e',code],capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertIn('FRONTEND_OK',result.stdout)


if __name__=='__main__':unittest.main()
