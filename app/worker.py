"""Each parse/TTS runs in a disposable subprocess with a wall-clock limit."""
import sys
from pathlib import Path
from app.extract import extract
from app.tts import synthesize

if __name__ == '__main__':
    mode, source, target, *args = sys.argv[1:]
    if mode == 'extract':
        try:
            Path(target).write_text(extract(Path(source).read_bytes(), args[0]), encoding='utf-8')
        except ValueError as exc:
            Path(target + '.error').write_text(str(exc), encoding='utf-8')
            sys.exit(2)
    else:
        synthesize(Path(source).read_text(encoding='utf-8'), args[0], args[1], Path(target))
