"""Directional SSH throughput probes, timed after authentication with bounded traffic."""
import json
import os
import selectors
import shlex
import subprocess
import threading
import time

PROBE_LOCK = threading.Lock()
PROBE_SCRIPT = r'''
import os,sys,time,json
direction=sys.argv[1];limit=int(sys.argv[2])
sys.stdout.buffer.write(b'READY\n');sys.stdout.buffer.flush()
if direction=='download':
 block=os.urandom(65536);total=0
 while total<limit:
  sys.stdout.buffer.write(block[:min(len(block),limit-total)]);sys.stdout.buffer.flush();total+=min(len(block),limit-total)
else:
 total=0;started=time.monotonic();last=started
 while total<limit:
  block=os.read(sys.stdin.fileno(),min(65536,limit-total))
  if not block:break
  total+=len(block);now=time.monotonic()
  if now-last>=.1 or total==limit:
   print(json.dumps([total,now-started]),flush=True);last=now
 if total<limit:print(json.dumps([total,time.monotonic()-started]),flush=True)
'''


def sample(route, cancel, direction, ssh_args, ssh_env, stop, seconds=3, limit=16*1024*1024):
    if cancel and cancel.is_set(): raise ValueError('任务已取消')
    command = 'python3 -u -c ' + shlex.quote(PROBE_SCRIPT) + ' ' + direction + ' ' + str(limit)
    process = subprocess.Popen(ssh_args(route) + [route['host'], command], stdin=subprocess.PIPE,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True, env=ssh_env(route))
    selector = selectors.DefaultSelector()
    selector.register(process.stdout, selectors.EVENT_READ, 'output')
    os.set_blocking(process.stdout.fileno(), False)
    os.set_blocking(process.stdin.fileno(), False)
    started = None
    deadline = time.monotonic() + 12
    count = sent = 0
    buffer = b''
    payload = os.urandom(65536)
    try:
        while time.monotonic() < deadline:
            if cancel and cancel.is_set():
                raise ValueError('任务已取消')
            events = selector.select(.1)
            for key, _ in events:
                if key.data == 'input':
                    try:
                        sent += os.write(process.stdin.fileno(), payload[:min(len(payload), limit-sent)])
                    except BlockingIOError:
                        continue
                    except BrokenPipeError:
                        selector.unregister(process.stdin)
                        continue
                    if sent >= limit:
                        selector.unregister(process.stdin)
                        process.stdin.close()
                    continue
                block = os.read(process.stdout.fileno(), 65536)
                if not block:
                    selector.unregister(process.stdout)
                    continue
                if started is None:
                    buffer += block
                    if b'\n' not in buffer:
                        if len(buffer) > 1024: raise ValueError('线路测速握手异常')
                        continue
                    marker, block = buffer.split(b'\n', 1)
                    if marker != b'READY': raise ValueError('线路测速握手异常')
                    buffer = b''
                    started = time.monotonic()
                    deadline = started + seconds
                    if direction == 'upload':
                        selector.register(process.stdin, selectors.EVENT_WRITE, 'input')
                if direction == 'download':
                    count += len(block)
                else:
                    buffer += block
                    while b'\n' in buffer:
                        line, buffer = buffer.split(b'\n', 1)
                        received, _ = json.loads(line)
                        count = max(count, received)
            if count >= limit or (process.poll() is not None and not selector.get_map()):
                break
        elapsed = max(time.monotonic() - started, .001) if started is not None else 0
        if process.poll() not in (None, 0) or (count < limit and elapsed < seconds - .1):
            raise ValueError('测速连接提前中断，保留线路以便实际传输重试')
        # Only remote acknowledgements count for upload; pipe buffering is not throughput.
        if count < 65536 or not elapsed:
            raise ValueError('线路测速没有收到足够数据或连接超时')
        return count / elapsed
    finally:
        selector.close()
        stop(process)
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
        for stream in (process.stdin, process.stdout, process.stderr):
            stream.close()


def measure(route, cancel, direction, ssh_args, ssh_env, stop):
    if direction not in ('upload', 'download'):
        raise ValueError('测速方向不正确')
    while not PROBE_LOCK.acquire(timeout=.1):
        if cancel and cancel.is_set(): raise ValueError('任务已取消')
    try:
        scores = [sample(route, cancel, direction, ssh_args, ssh_env, stop) for _ in range(2)]
        # A transient burst must not win over a route with sustained capacity.
        return min(scores)
    finally:
        PROBE_LOCK.release()
