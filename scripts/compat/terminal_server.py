"""Optional standalone terminal service, useful while transfers keep running."""
import argparse
import os
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import app

if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port',type=int,default=8767)
    args=parser.parse_args()
    token=os.environ.get('TERMIUSPLUS_TERMINAL_TOKEN')
    if not token:
        parser.error('请设置 TERMIUSPLUS_TERMINAL_TOKEN，与文件工具的访问凭证一致')
    app.TOKEN=token
    app.HISTORY_PATH=app.STATE_ROOT / 'terminal-service-history.json'
    import sys
    sys.argv=['app.py','--port',str(args.port)]
    app.main()
