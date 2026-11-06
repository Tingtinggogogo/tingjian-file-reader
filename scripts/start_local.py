"""Convenient loopback-only development server; secrets stay in ignored data/."""
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
os.chdir(ROOT)
DATA = ROOT / 'data'
DATA.mkdir(exist_ok=True)
config = DATA / 'local-settings.json'
if not config.exists():
    fd = os.open(config, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w') as out:
        json.dump({'SESSION_SECRET': secrets.token_hex(32), 'ACCESS_CODE': secrets.token_hex(12)}, out)
settings = json.loads(config.read_text())
env = os.environ.copy()
env.update(settings)
env.update({'COOKIE_SECURE': 'false', 'ALLOWED_ORIGINS': 'http://127.0.0.1:8000,http://localhost:8000', 'ENABLE_EDGE_TTS': 'false'})
print('\n听见文件 · 本机预览\n地址：http://127.0.0.1:8000\n访问口令：' + settings['ACCESS_CODE'] + '\n关闭此窗口即可停止。请勿将此口令用于公网部署。\n', flush=True)
try:
    subprocess.run([sys.executable, '-m', 'uvicorn', 'app.main:app', '--host', '127.0.0.1', '--port', '8000', '--workers', '1', '--no-access-log'], env=env, check=True)
except KeyboardInterrupt:
    pass
