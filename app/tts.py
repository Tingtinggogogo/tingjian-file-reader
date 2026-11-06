import asyncio
import shutil
import sys
import subprocess
from pathlib import Path

VOICES = {'female': 'zh-CN-XiaoxiaoNeural', 'male': 'zh-CN-YunxiNeural'}


def synthesize(text: str, voice: str, provider: str, target: Path):
    if provider == 'edge':
        import edge_tts
        asyncio.run(asyncio.wait_for(edge_tts.Communicate(text, VOICES[voice]).save(str(target)), timeout=180))
    elif provider == 'offline':
        # Fixed argv; uploaded text is stdin, never a shell command.
        if shutil.which('espeak-ng'):
            command = ['espeak-ng', '-v', 'cmn', '-s', '165', '-w', str(target), '--stdin']
        elif sys.platform == 'darwin':
            command = ['/usr/bin/say', '-v', 'Tingting', '--file-format=WAVE',
                       '--data-format=LEI16@22050', '-o', str(target)]
        else:
            raise RuntimeError('请安装 eSpeak NG')
        subprocess.run(command, input=text.encode(), check=True, timeout=180, capture_output=True)
    else:
        raise ValueError('不支持的语音引擎')
    if not target.exists() or target.stat().st_size == 0:
        raise RuntimeError('未生成音频')
