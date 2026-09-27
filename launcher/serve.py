"""TermiusPlus.app 的服务入口：与 app.py 参数相同；App 退出或崩溃（标准输入管道关闭）时自动停止服务。"""
import os
import signal
import sys
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.chdir(str(ROOT))

# App 持有管道写端；子进程（rsync、ssh、终端）改用 /dev/null 作为标准输入。
watch_fd = os.dup(0)
devnull = os.open(os.devnull, os.O_RDONLY)
os.dup2(devnull, 0)
os.close(devnull)


def watch_parent():
    try:
        while os.read(watch_fd, 4096):
            pass
    except OSError:
        pass
    os.kill(os.getpid(), signal.SIGTERM)


threading.Thread(target=watch_parent, daemon=True).start()

import app  # noqa: E402

sys.argv = ['app.py'] + sys.argv[1:]
app.main()
