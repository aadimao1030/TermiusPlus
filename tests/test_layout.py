"""Exercise the real resize handlers, including persisted layout migration."""
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class LayoutTests(unittest.TestCase):
    def test_both_panes_resize_and_restore_together(self):
        source = (ROOT / 'web/scripts/files.js').read_text()
        script = source[source.index('let layout = {};'):source.index('new ResizeObserver(applyLayout)')]
        harness = r'''
const assert = require('node:assert/strict');
const elements = new Map();
const $ = id => {
  if (!elements.has(id)) elements.set(id, {style:{},attrs:{},clientWidth:1010,offsetHeight:0,hidden:false,
    setAttribute(k,v){this.attrs[k]=v},setPointerCapture(){}});
  return elements.get(id);
};
const sides = ['local','remote'];
$('.topbar').offsetHeight=50;
$('.statusbar').offsetHeight=26;
const getComputedStyle = () => ({paddingTop:'12px',paddingBottom:'12px'});
const window = {innerHeight:900};
const document = {querySelector:s=>$(s),body:{classList:{add(){},remove(){}}}};
let stored = JSON.stringify({localHeight:400,remoteHeight:600,queueHeight:220});
const localStorage = {getItem(){return stored},setItem(k,v){stored=v}};
'''
        checks = r'''
applyLayout();
assert.equal($('localPane').style.height,'800px');
assert.equal($('remotePane').style.height,'800px');
for (const id of ['localResizer','remoteResizer']) {
  const handle=$(id), initial=layout.localHeight;
  handle.onpointerdown({button:0,pointerId:1,clientY:100,preventDefault(){}});
  handle.onpointermove({pointerId:1,clientY:180});
  handle.onpointerup();
  assert.equal(layout.localHeight,initial+80);
  assert.equal(layout.remoteHeight,layout.localHeight);
  assert.equal(JSON.parse(stored).remoteHeight,layout.localHeight);
  handle.onkeydown({key:'ArrowDown',preventDefault(){}});
  assert.equal(layout.localHeight,initial+120);
  assert.equal(layout.remoteHeight,layout.localHeight);
  handle.ondblclick();
  assert.equal(layout.localHeight,800);
  assert.equal(layout.remoteHeight,800);
}
assert.equal(layout.queueHeight,undefined);
assert.equal(elements.has('queueResizer'),false);
assert.equal(layout.localHeight,800);
$('localResizer').onpointerdown({button:0,pointerId:2,clientY:100,preventDefault(){}});
$('localResizer').onpointermove({pointerId:2,clientY:-1000});
$('localResizer').onpointerup();
assert.equal(layout.localHeight,800);
assert.equal(layout.remoteHeight,800);
assert.equal(layout.paneExtraHeight,0);
// Resizing the window fills available space without needing to reset old sizes.
window.innerHeight=1100;
applyLayout();
assert.equal(layout.localHeight,1000);
assert.equal($('remotePane').style.height,'1000px');
window.innerHeight=600;
$('.topbar').offsetHeight=70;
applyLayout();
assert.equal(layout.localHeight,480);
assert.equal($('localPane').style.height,'480px');
// Extra height selected by dragging is retained across window resizing.
setLayoutValue('remoteHeight',580);
saveLayout();
window.innerHeight=700;
applyLayout();
assert.equal(layout.localHeight,680);
assert.equal(layout.remoteHeight,680);
assert.equal(JSON.parse(stored).paneExtraHeight,100);
// Hidden workspaces must not use their zero-sized toolbar/footer measurements.
$('fileWorkspace').hidden=true;
window.innerHeight=900;
applyLayout();
assert.equal(layout.localHeight,680);

'''
        result = subprocess.run(['node', '-e', harness + script + checks], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == '__main__':
    unittest.main()
