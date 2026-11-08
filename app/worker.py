"""Each parse/TTS runs in a disposable subprocess with a wall-clock limit."""
import sys
import json
from pathlib import Path
from app.tts import synthesize

if __name__ == '__main__':
    mode, source, target, *args = sys.argv[1:]
    if mode == 'extract':
        from app.extract import extract
        from app.ebooks import epub_text, mobi_text
        try:
            path = Path(source)
            marks = []
            ext = Path(args[0]).suffix.lower()
            if ext == '.epub':
                content = epub_text(path, marks)
            elif ext == '.mobi':
                content = mobi_text(path, path.parent, marks)
            else:
                content = extract(path.read_bytes(), args[0], path.parent, marks)
            if not marks and ext in {'.epub','.mobi','.pdf','.docx','.md','.txt'}:
                from app.chapters import text_marks
                marks = text_marks(content)
            Path(target).write_text(content, encoding='utf-8')
            Path(target + '.outline').write_text(json.dumps(marks, ensure_ascii=False), encoding='utf-8')
        except ValueError as exc:
            Path(target + '.error').write_text(str(exc), encoding='utf-8')
            sys.exit(2)
    else:
        synthesize(Path(source).read_text(encoding='utf-8'), args[0], args[1], Path(target), args[2] if len(args) > 2 else 'normal')
