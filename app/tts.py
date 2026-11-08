import asyncio
import json
import re
import math
import shutil
import sys
import subprocess
from pathlib import Path

VOICES = {'female': 'zh-CN-XiaoxiaoNeural', 'xiaoyi': 'zh-CN-XiaoyiNeural', 'male': 'zh-CN-YunxiNeural'}
RATES = {'slow': '-15%', 'normal': '+0%', 'fast': '+15%'}
PARALLEL_PARTS = 3
WORDS_PER_MINUTE = {'slow': '140', 'normal': '165', 'fast': '190'}


def split_text(text: str) -> list[str]:
    """At most three balanced parts, preserving every character and punctuation."""
    if len(text) <= 80:
        return [text]
    boundaries = [match.end() for match in re.finditer(r'[。！？!?；;\n]+', text)]
    cuts = [0]
    for index in range(1, PARALLEL_PARTS):
        goal = len(text) * index // PARALLEL_PARTS
        candidates = [p for p in boundaries if cuts[-1] < p < len(text)
                      and abs(p - goal) <= min(250, len(text) // 8)]
        if len(text) <= 240 and not candidates:
            return [text]
        cut = min(candidates, key=lambda p: abs(p - goal)) if candidates else goal
        cuts.append(cut)
    cuts.append(len(text))
    parts = [text[a:b] for a, b in zip(cuts, cuts[1:])]
    return parts if all(part.strip() for part in parts) else [text]


def speech_timeout(length):
    return 180 * max(1, math.ceil(length / 5000))


def synthesis_parts(content):
    if len(content) <= 5000:
        return split_text(content)
    chunks = []
    while len(content) > 1800:
        window = content[:1800]
        end = max((window.rfind(mark)+1 for mark in '。！？；\n'),default=0)
        end = end if end >= 900 else 1800
        chunks.append(content[:end])
        content = content[end:]
    chunks.append(content)
    return [chunk for chunk in chunks if chunk.strip()]


async def synthesize_edge(text, voice, target, rate):
    import edge_tts
    parts = synthesis_parts(text)
    progress = target.parent / 'progress.json'
    completed = 0

    def report():
        temporary = progress.with_suffix('.tmp')
        temporary.write_text(json.dumps({'completed': completed, 'total': len(parts)}))
        temporary.replace(progress)

    report()
    if len(parts) == 1:
        await edge_tts.Communicate(text, VOICES[voice], rate=RATES[rate]).save(str(target))
        completed = 1
        report()
        return
    paths = [target.parent / f'part-{index}.mp3' for index in range(len(parts))]

    concurrency = asyncio.Semaphore(PARALLEL_PARTS)

    async def generate(index):
        nonlocal completed
        async with concurrency:
            await edge_tts.Communicate(parts[index], VOICES[voice], rate=RATES[rate]).save(str(paths[index]))
        completed += 1
        report()

    try:
        async with asyncio.TaskGroup() as group:
            for index in range(len(parts)):
                group.create_task(generate(index))
        listing = target.parent / 'concat.txt'
        listing.write_text(''.join(f"file '{path.name}'\n" for path in paths))
        # Stream-copy consistent MP3 frames; no resampling or voice-speed change.
        subprocess.run(['ffmpeg', '-nostdin', '-v', 'error', '-y', '-f', 'concat', '-safe', '1',
                        '-i', str(listing), '-map_metadata', '-1', '-c:a', 'copy', str(target)],
                       check=True, timeout=max(15,len(parts)), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    finally:
        for path in paths:
            path.unlink(missing_ok=True)
        (target.parent / 'concat.txt').unlink(missing_ok=True)


def synthesize(text: str, voice: str, provider: str, target: Path, rate: str = 'normal'):
    if voice not in VOICES or rate not in RATES:
        raise ValueError('不支持的音色或语速')
    if provider == 'edge':
        asyncio.run(asyncio.wait_for(synthesize_edge(text, voice, target, rate), timeout=speech_timeout(len(text))))
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
