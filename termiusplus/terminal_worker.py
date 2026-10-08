"""Give the child a controlling terminal before executing SSH."""
import fcntl
import os
import sys
import termios
import json

if __name__ == '__main__':
    fcntl.ioctl(0, termios.TIOCSCTTY, 0)
    if sys.argv[1] == '--reconnect-ssh':
        from terminal_reconnect import run
        sys.exit(run(json.loads(sys.argv[2])))
    os.execvpe(sys.argv[1], sys.argv[1:], os.environ)
