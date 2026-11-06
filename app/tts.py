import asyncio
import shutil
import sys
import subprocess
from pathlib import Path

VOICES = {'female': 'zh-CN-XiaoxiaoNeural', 'xiaoyi': 'zh-CN-XiaoyiNeural', 'male': 'zh-CN-YunxiNeural'}
RATES = {'slow': '-15%', 'normal': '+0%', 'fast': '+15%'}
WORDS_PER_MINUTE = {'slow': '140', 'normal': '165', 'fast': '190'}


def synthesize(text: str, voice: str, provider: str, target: Path, rate: str = 'normal'):
    if voice not in VOICES or rate not in RATES:
        raise ValueError('不支持的音色或语速')
    if provider == 'edge':
        import edge_tts
        asyncio.run(asyncio.wait_for(edge_tts.Communicate(text, VOICES[voice], rate=RATES[rate]).save(str(target)), timeout=180))
    elif provider == 'offline':
        # Fixed argv; uploaded text is stdin, never a shell command.
        if shutil.which('espeak-ng'):
            command = ['espeak-ng', '-v', 'cmn', '-s', WORDS_PER_MINUTE[rate], '-w', str(target), '--stdin']
        elif sys.platform == 'darwin':
            command = ['/usr/bin/say', '-v', 'Tingting', '--file-format=WAVE',
                       '--data-format=LEI16@22050', '-r', WORDS_PER_MINUTE[rate], '-o', str(target)]
        else:
            raise RuntimeError('请安装 eSpeak NG')
        subprocess.run(command, input=text.encode(), check=True, timeout=180, capture_output=True)
    else:
        raise ValueError('不支持的语音引擎')
    if not target.exists() or target.stat().st_size == 0:
        raise RuntimeError('未生成音频')
