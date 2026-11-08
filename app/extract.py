"""Bounded extraction. Never execute uploads or fetch embedded resources."""
import io
import json
import zipfile
from pathlib import Path
from PIL import Image

SUPPORTED = {'.epub', '.mobi', '.txt', '.md', '.csv', '.json', '.log', '.pdf', '.docx', '.xlsx', '.pptx', '.png', '.jpg', '.jpeg', '.webp'}
MAX_TEXT = 30000


def extract(data: bytes, filename: str, temp_parent: Path | None = None) -> str:
    ext = Path(filename).suffix.lower()
    if ext not in SUPPORTED:
        raise ValueError('暂不支持此类型。请转换为 EPUB、MOBI、PDF、DOCX、TXT、XLSX、PPTX 或图片。')
    if ext in {'.docx', '.xlsx', '.pptx'}:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            entries = archive.infolist()
            if len(entries) > 3000 or sum(x.file_size for x in entries) > 80 * 1024 * 1024:
                raise ValueError('文档解压后过大，请拆分文件。')
            if any(x.flag_bits & 1 for x in entries):
                raise ValueError('不支持加密文档。')
    if ext in {'.epub', '.mobi'}:
        from app.ebooks import epub_text, mobi_text
        return epub_text(data) if ext == '.epub' else mobi_text(data, temp_parent)
    elif ext == '.pdf':
        from pypdf import PdfReader
        if not data.startswith(b'%PDF-'):
            raise ValueError('文件内容与 PDF 格式不符。')
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted:
            raise ValueError('请先移除 PDF 密码。')
        if len(reader.pages) > 100:
            raise ValueError('PDF 最多 100 页，请拆分。')
        chunks, margins, size = [], [], 0
        for page in reader.pages:
            top, bottom = set(), set()
            box = page.cropbox
            low, high = float(box.bottom), float(box.top)
            band = (high - low) * 0.10

            def locate(fragment, cm, tm, font, font_size):
                line = fragment.strip()
                if not line or len(line.splitlines()) != 1 or page.rotation:
                    return
                y = tm[4] * cm[1] + tm[5] * cm[3] + cm[5]
                if high - band <= y <= high:
                    top.add(line)
                elif low <= y <= low + band:
                    bottom.add(line)

            chunk = page.extract_text(visitor_text=locate) or ''
            margins.append((top, bottom))
            size += len(chunk)
            if size > MAX_TEXT:
                raise ValueError('提取文字超过 30000 字，请拆分文件。')
            chunks.append(chunk)
        from app.reading_text import pdf_body
        text = pdf_body(chunks, margins)
    elif ext == '.docx':
        from docx import Document
        doc = Document(io.BytesIO(data))
        text = '\n'.join([p.text for p in doc.paragraphs] + [' '.join(c.text for c in row.cells) for table in doc.tables for row in table.rows])
    elif ext == '.xlsx':
        from openpyxl import load_workbook
        book = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
        chunks, size, cells = [], 0, 0
        try:
            for sheet in book:
                for row in sheet.iter_rows():
                    cells += len(row)
                    if cells > 100000:
                        raise ValueError('表格过大，请缩小范围。')
                    line = '，'.join(str(c.value) for c in row if c.value is not None)
                    size += len(line) + 1
                    if size > MAX_TEXT:
                        raise ValueError('提取文字超过 30000 字，请拆分文件。')
                    chunks.append(line)
        finally:
            book.close()
        text = '\n'.join(chunks)
    elif ext == '.pptx':
        from pptx import Presentation
        from pptx.enum.shapes import PP_PLACEHOLDER
        slides = Presentation(io.BytesIO(data)).slides
        margins = {PP_PLACEHOLDER.HEADER, PP_PLACEHOLDER.FOOTER, PP_PLACEHOLDER.SLIDE_NUMBER}
        text = '\n'.join(s.text for slide in slides for s in slide.shapes if s.has_text_frame
                         and not (s.is_placeholder and s.placeholder_format.type in margins))
    elif ext in {'.png', '.jpg', '.jpeg', '.webp'}:
        import pytesseract
        Image.MAX_IMAGE_PIXELS = 16000000
        with Image.open(io.BytesIO(data)) as im:
            if im.width * im.height > 16000000:
                raise ValueError('图片最多 1600 万像素。')
            text = pytesseract.image_to_string(im, lang='chi_sim+eng', timeout=40)
    else:
        text = None
        for encoding in ('utf-8-sig', 'gb18030', 'utf-16'):
            try:
                if encoding == 'utf-16' and not data.startswith((b'\xff\xfe', b'\xfe\xff')):
                    continue
                candidate = data.decode(encoding)
                if not any(ord(c) < 32 and c not in '\n\r\t\f' for c in candidate):
                    text = candidate
                    break
            except UnicodeError:
                pass
        if text is None:
            raise ValueError('无法识别文本编码，请转换为 UTF-8。')
        if ext == '.json':
            text = json.dumps(json.loads(text), ensure_ascii=False, indent=2)
    text = text.strip()
    if not text:
        raise ValueError('没有提取到文字。扫描 PDF 请先转换为图片后上传识字。')
    if len(text) > MAX_TEXT:
        raise ValueError('提取文字超过 30000 字，请拆分文件。')
    return text
