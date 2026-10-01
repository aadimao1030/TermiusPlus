"""Folder click timing must leave a third click time to enter the folder."""
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class FolderClickTests(unittest.TestCase):
    def test_double_expands_after_grace_period_and_triple_enters(self):
        source = (ROOT / 'web/scripts/files.js').read_text()
        helper = source[source.index('const folderClickDelay = 520;'):source.index('function relative(root, path)')]
        script = r'''
const assert = require('node:assert/strict');
let now = 0, nextId = 0;
const jobs = new Map();
const performance = {now: () => now};
const setTimeout = (callback, delay) => {
  const id = ++nextId;
  jobs.set(id, {callback, due: now + delay});
  return id;
};
const clearTimeout = (id) => jobs.delete(id);
function advance(ms) {
  now += ms;
  for (const [id, job] of [...jobs]) {
    if (job.due <= now && jobs.delete(id)) job.callback();
  }
}
''' + helper + r'''
const row = {isConnected: true};
const actions = [];
const click = () => handleFolderClick(row, () => actions.push('expand'), () => actions.push('enter'));
click(); advance(150); click();
assert.deepEqual(actions, []);
advance(500);
assert.deepEqual(actions, []);
advance(20);
assert.deepEqual(actions, ['expand']);
cancelFolderClick(); actions.length = 0;
click(); advance(180); click(); advance(400); click();
assert.deepEqual(actions, ['enter']);
advance(1000);
assert.deepEqual(actions, ['enter']);
'''
        result = subprocess.run(['node', '-e', script], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == '__main__':
    unittest.main()
