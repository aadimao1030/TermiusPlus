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
  if (!elements.has(id)) elements.set(id, {style:{},attrs:{},clientWidth:1010,
    setAttribute(k,v){this.attrs[k]=v},setPointerCapture(){}});
  return elements.get(id);
};
const sides = ['local','remote'];
const window = {innerHeight:900};
const document = {querySelector:s=>$(s),body:{classList:{add(){},remove(){}}}};
let stored = JSON.stringify({localHeight:400,remoteHeight:600,queueHeight:220});
const localStorage = {getItem(){return stored},setItem(k,v){stored=v}};
'''
        checks = r'''
applyLayout();
assert.equal($('localPane').style.height,'600px');
assert.equal($('remotePane').style.height,'600px');
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
  assert.equal(layout.localHeight,612);
  assert.equal(layout.remoteHeight,612);
}
assert.equal(layout.queueHeight,undefined);
assert.equal(elements.has('queueResizer'),false);
assert.equal(layout.localHeight,612);
$('localResizer').onpointerdown({button:0,pointerId:2,clientY:100,preventDefault(){}});
$('localResizer').onpointermove({pointerId:2,clientY:-1000});
$('localResizer').onpointerup();
assert.equal(layout.localHeight,260);
assert.equal(layout.remoteHeight,260);
'''
        result = subprocess.run(['node', '-e', harness + script + checks], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == '__main__':
    unittest.main()
