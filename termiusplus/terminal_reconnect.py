"""Reconnect SSH transports while the remote tmux shell stays alive."""
import os
import signal
import subprocess
import sys
import termios
import threading
import time

FATAL_ERRORS = (b'permission denied', b'host key verification failed',
                b'remote host identification has changed', b'too many authentication failures',
                b'bad configuration option', b'bad owner or permissions',
                b'no matching host key type',
                b'no matching key exchange method')


def run(command):
    # A caught (rather than ignored) handler becomes the default in exec'd SSH.
    # Ctrl+C belongs to the interactive child, not the reconnect supervisor.
    signal.signal(signal.SIGINT, lambda *_: None)
    attempt = 0
    while True:
        try:
            # Never replay commands typed while the transport was disconnected.
            if attempt and os.isatty(0):
                termios.tcflush(0, termios.TCIFLUSH)
            started = time.monotonic()
            child = subprocess.Popen(command, stderr=subprocess.PIPE)
        except OSError as exc:
            print('[无法启动 SSH：{}]'.format(exc), file=sys.stderr, flush=True)
            return 127
        errors = bytearray()

        def stderr():
            try:
                while True:
                    block = os.read(child.stderr.fileno(), 4096)
                    if not block:
                        break
                    errors.extend(block)
                    del errors[:-16384]
                    sys.stderr.buffer.write(block)
                    sys.stderr.buffer.flush()
            finally:
                child.stderr.close()

        reader = threading.Thread(target=stderr, daemon=True)
        reader.start()
        code = child.wait()
        reader.join()
        # SSH uses 255 for transport/authentication errors. A normal remote shell
        # exit, tmux detach or configuration/auth failure must not restart it.
        if code != 255 or any(error in bytes(errors).lower() for error in FATAL_ERRORS):
            return code
        if time.monotonic() - started >= 60:
            attempt = 0
        wait = min(2 ** min(attempt + 1, 5), 30)
        attempt += 1
        print('\r\n[SSH 线路已断开，{} 秒后自动重连；服务器支持 tmux 时会恢复原会话。'
              '重连期间请勿输入命令。]\r\n'.format(wait), flush=True)
        time.sleep(wait)
