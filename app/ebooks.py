"""Read ebook text locally; never render HTML, fetch links, or unpack ZIP paths."""
import io
import posixpath
import shutil
import struct
import subprocess
import sys
import tempfile
import zipfile
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urlsplit

from defusedxml import ElementTree as ET

MAX_EXPANDED = 256 * 1024 * 1024
MAX_TEXT = 300000


def member_path(base: str, href: str) -> str:
    url = urlsplit(href)
    path = unquote(url.path)
    if url.scheme or url.netloc or url.query or not path or '\\' in path or '\x00' in path or path.startswith('/'):
        raise ValueError('电子书包含无效或外部章节路径。')
    resolved = posixpath.normpath(posixpath.join(base, path))
    if resolved == '..' or resolved.startswith('../'):
        raise ValueError('电子书章节路径越界。')
    return resolved


class ChapterText(HTMLParser):
    BLOCK = {'p', 'div', 'br', 'section', 'article', 'li', 'tr', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'blockquote', 'hr'}
    SKIP = {'head', 'script', 'style', 'nav', 'noscript', 'svg'}
    VOID = {'area', 'base', 'br', 'col', 'embed', 'hr', 'img', 'input', 'link', 'meta', 'param', 'source', 'track', 'wbr'}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack, self.chunks = [], []

    def add(self, value):
        self.chunks.append(value)

    def handle_starttag(self, tag, attrs):
        tag = tag.split(':')[-1]
        attributes = dict(attrs)
        hidden = tag in self.SKIP or 'hidden' in attributes or attributes.get('aria-hidden') == 'true'
        hidden = hidden or bool(self.stack and self.stack[-1][1])
        if not hidden and tag in self.BLOCK:
            self.add('\n')
        if tag not in self.VOID:
            self.stack.append((tag, hidden))

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag.split(':')[-1] not in self.VOID:
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        tag = tag.split(':')[-1]
        hidden = bool(self.stack and self.stack[-1][1])
        for index in range(len(self.stack) - 1, -1, -1):
            if self.stack[index][0] == tag:
                del self.stack[index:]
                break
        if not hidden and tag in self.BLOCK:
            self.add('\n')

    def handle_data(self, data):
        if not self.stack or not self.stack[-1][1]:
            # Collapse formatting indentation without separating inline Chinese.
            import re
            self.add(re.sub(r'\s+', ' ', data))


def chapter_text(raw: bytes) -> str:
    encoding = 'utf-16' if raw.startswith((b'\xff\xfe', b'\xfe\xff')) else 'utf-8-sig'
    parser = ChapterText()
    try:
        parser.feed(raw.decode(encoding))
        parser.close()
    except UnicodeError as exc:
        raise ValueError('电子书文字编码无效，请转换为 UTF-8 EPUB。') from exc
    return '\n'.join(line.strip() for line in ''.join(parser.chunks).splitlines() if line.strip())


def epub_text(data: bytes | Path) -> str:
    try:
        with zipfile.ZipFile(data if isinstance(data, Path) else io.BytesIO(data)) as book:
            def read_member(name):
                if book.getinfo(name).file_size > 8 * 1024 * 1024:
                    raise ValueError('单个电子书章节过大，请重新划分章节。')
                return book.read(name)
            entries = book.infolist()
            names = [entry.filename for entry in entries]
            if len(entries) > 3000 or sum(entry.file_size for entry in entries) > MAX_EXPANDED:
                raise ValueError('电子书解压后过大，请拆分文件。')
            if len(set(names)) != len(names):
                raise ValueError('电子书包含重复文件路径。')
            if any(entry.flag_bits & 1 for entry in entries):
                raise ValueError('不支持加密或 DRM 保护的电子书。')
            if read_member('mimetype').strip() != b'application/epub+zip':
                raise ValueError('文件内容与 EPUB 格式不符。')
            container = ET.fromstring(read_member('META-INF/container.xml'))
            rootfile = container.find('.//{*}rootfile')
            if rootfile is None:
                raise ValueError('EPUB 缺少章节目录。')
            package_path = member_path('', rootfile.get('full-path', ''))
            package = ET.fromstring(read_member(package_path))
            base = posixpath.dirname(package_path)
            manifest = {item.get('id'): item for item in package.findall('./{*}manifest/{*}item')}
            encrypted = set()
            if 'META-INF/encryption.xml' in names:
                for ref in ET.fromstring(read_member('META-INF/encryption.xml')).findall('.//{*}CipherReference'):
                    encrypted.add(member_path('', ref.get('URI', '')))
            chunks, size = [], 0
            spine = package.findall('./{*}spine/{*}itemref')
            if not spine:
                raise ValueError('EPUB 缺少正文阅读顺序。')
            for ref in spine:
                if ref.get('linear', 'yes') == 'no':
                    continue
                item = manifest.get(ref.get('idref'))
                if item is None or item.get('media-type') not in {'application/xhtml+xml', 'text/html'}:
                    raise ValueError('EPUB 正文章节缺失或不是可朗读文字。')
                path = member_path(base, item.get('href', ''))
                if path in encrypted:
                    raise ValueError('不支持加密或 DRM 保护的电子书。')
                chunk = chapter_text(read_member(path))
                if not chunk:
                    continue
                size += len(chunk) + (1 if chunks else 0)
                if size > MAX_TEXT:
                    raise ValueError('提取文字超过 30 万字，请拆分电子书。')
                chunks.append(chunk)
            result = '\n'.join(chunks)
            if not result:
                raise ValueError('电子书没有可提取的正文文字，纯图片电子书暂不支持。')
            return result
    except ValueError:
        raise
    except Exception as exc:
        raise ValueError('EPUB 已损坏或章节目录无效，请重新导出后上传。') from exc


def mobi_text(data: bytes | Path, temp_parent: Path | None = None) -> str:
    # Validate the Palm database before starting the native decoder.
    size = data.stat().st_size if isinstance(data, Path) else len(data)
    with (data.open('rb') if isinstance(data, Path) else io.BytesIO(data)) as stream:
        palm = stream.read(78)
        if size < 86 or palm[60:68] != b'BOOKMOBI':
            raise ValueError('文件内容与 MOBI 格式不符。')
        count = struct.unpack_from('>H', palm, 76)[0]
        if not 1 <= count <= 10000 or 78 + 8 * count > size:
            raise ValueError('MOBI 记录目录无效或过大。')
        directory = stream.read(8 * count)
        offsets = [struct.unpack_from('>I', directory, i * 8)[0] for i in range(count)]
        if offsets[0] < 78 + 8 * count or offsets[-1] >= size or any(a >= b for a, b in zip(offsets, offsets[1:])):
            raise ValueError('MOBI 文件已损坏。')
        if offsets[0] + 16 > (offsets[1] if count > 1 else size):
            raise ValueError('MOBI 文件头不完整。')
        stream.seek(offsets[0])
        header = stream.read(16)
    if struct.unpack_from('>H', header, 12)[0]:
        raise ValueError('不支持加密或 DRM 保护的电子书。')
    if struct.unpack_from('>I', header, 4)[0] > MAX_EXPANDED:
        raise ValueError('电子书解压后过大，请拆分文件。')
    if not shutil.which('mobitool'):
        raise ValueError('MOBI 解析组件尚未安装，请联系管理员。')
    # The temporary directory lives beside the worker input and is also removed
    # by the request's upload-directory cleanup if the worker is interrupted.
    with tempfile.TemporaryDirectory(prefix='mobi-', dir=temp_parent) as temp:
        root = Path(temp)
        source = root / 'book.mobi'
        if isinstance(data, Path):
            shutil.copyfile(data, source)
        else:
            source.write_bytes(data)
        try:
            subprocess.run([sys.executable, '-m', 'app.mobi_convert', str(source), str(root)],
                           check=True, timeout=45, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except (subprocess.SubprocessError, OSError) as exc:
            raise ValueError('MOBI 已损坏、受 DRM 保护或解析超时，请使用未加密的 EPUB。') from exc
        output = root / 'book.epub'
        if not output.is_file() or output.stat().st_size > MAX_EXPANDED:
            raise ValueError('MOBI 未能转换为可朗读电子书，或内容过大。')
        return epub_text(output)
