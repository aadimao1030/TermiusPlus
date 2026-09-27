import base64
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import app


class PreviewTests(unittest.TestCase):
    def test_text_symlink_and_html_are_plain_text(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d)/'中文.html'; text = '<script>alert("sample")</script>\n你好'
            p.write_text(text, encoding='utf-8')
            link = Path(d)/'link'; link.symlink_to(p)
            result = app.file_preview(str(link))
            self.assertEqual(result['kind'], 'text')
            self.assertEqual(result['content'], text)
            self.assertFalse(result['truncated'])
            self.assertEqual(result['path'], str(p.resolve()))
            self.assertEqual(p.read_text(), text)

    def test_bounded_text_with_multibyte_boundary_and_bom(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d)/'large.log'; p.write_bytes(b'a'*(256*1024-1)+'你好'.encode())
            result = app.file_preview(str(p))
            self.assertEqual(result['kind'], 'text')
            self.assertTrue(result['truncated'])
            self.assertEqual(len(result['content']), 256*1024-1)
            p.write_bytes('中文'.encode('utf-16'))
            self.assertEqual(app.file_preview(str(p))['content'], '中文')

    def test_image_signature_and_size_limit(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d)/'image.dat'; content = b'\x89PNG\r\n\x1a\nexample'
            p.write_bytes(content)
            result = app.file_preview(str(p))
            self.assertEqual(result['kind'], 'image')
            self.assertEqual(result['mime'], 'image/png')
            self.assertEqual(base64.b64decode(result['content']), content)
            with p.open('r+b') as stream: stream.truncate(8*1024*1024+1)
            self.assertEqual(app.file_preview(str(p))['kind'], 'unsupported')

    def test_binary_and_non_regular_files(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d)/'binary'; p.write_bytes(b'abc\0\xff')
            self.assertEqual(app.file_preview(str(p))['kind'], 'unsupported')
            fifo = Path(d)/'pipe'; os.mkfifo(fifo)
            with self.assertRaises(ValueError): app.file_preview(str(fifo))
            with self.assertRaises((ValueError, OSError)): app.file_preview(d)
            with self.assertRaises(OSError): app.file_preview(str(Path(d)/'missing'))

    def test_remote_preview_quoted_path_and_shared_reader(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d)/"a' $(id).txt"; p.write_text('remote sample')
            output = subprocess.run([sys.executable, '-c', app.REMOTE_PREVIEW_SCRIPT, str(p)], capture_output=True, check=True)
            with patch('app.subprocess.run', return_value=output) as run:
                result = app.remote_preview({'host':'fixture'}, str(p))
                self.assertEqual(result['content'], 'remote sample')
                import shlex
                command = run.call_args.args[0][-1]
                self.assertEqual(shlex.split(command)[-1], str(p))
                self.assertEqual(run.call_args.kwargs['timeout'], 25)

if __name__ == '__main__': unittest.main()
