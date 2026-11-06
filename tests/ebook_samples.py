"""Small original Chinese fixtures generated at test time; no third-party books."""
import io
import struct
import zipfile


def epub_sample(chapters=None, extra=None, spine=None):
    chapters = chapters or {'second.xhtml': '<html><head><title>不要重复标题</title></head><body><p>第二章：晚安。</p></body></html>',
                            'first.xhtml': '<html><body><h1>第一章</h1><p>你好，<b>世界</b>。</p><script>不朗读脚本</script><style>不朗读样式</style></body></html>'}
    spine = spine or list(reversed(chapters))
    items = ''.join(f'<item id="i{index}" href="{name}" media-type="application/xhtml+xml"/>' for index, name in enumerate(chapters))
    refs = ''.join(f'<itemref idref="i{list(chapters).index(name)}"/>' for name in spine)
    files = {'mimetype': 'application/epub+zip',
             'META-INF/container.xml': '<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles><rootfile full-path="Books/package.opf"/></rootfiles></container>',
             'Books/package.opf': f'<package xmlns="http://www.idpf.org/2007/opf"><manifest>{items}</manifest><spine>{refs}</spine></package>',
             **{'Books/' + name: value for name, value in chapters.items()}, **(extra or {})}
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, 'w', zipfile.ZIP_DEFLATED) as archive:
        for name, value in files.items():
            archive.writestr(name, value)
    return stream.getvalue()


def mobi_sample(encrypted=False, compressed=False):
    html = '<html><head><title>听见测试</title></head><body><h1>第一章</h1><p>你好，欢迎使用听见文件。</p><p>第二章，晚安。</p></body></html>'.encode()
    # PalmDOC literal chunks are valid compressed text; no proprietary encoder.
    encoded = b''.join(bytes([len(html[i:i+8])]) + html[i:i+8] for i in range(0, len(html), 8)) if compressed else html
    name = '听见文件测试'.encode()
    mobi = bytearray(232)
    mobi[:4] = b'MOBI'
    for offset, value in {4:232, 8:2, 12:65001, 16:1234, 20:6, 64:2, 68:248, 72:len(name), 76:0x0804, 88:6, 92:0xffffffff, 148:0xffffffff, 228:0xffffffff}.items():
        struct.pack_into('>I', mobi, offset, value)
    for offset in range(24, 64, 4): struct.pack_into('>I', mobi, offset, 0xffffffff)
    record0 = struct.pack('>HHIHHHH', 2 if compressed else 1, 0, len(html), 1, 4096, int(encrypted), 0) + mobi + name
    header = struct.pack('>32sHHIIIIII4s4sIIH', b'Tingjian test', 0, 0, 0, 0, 0, 0, 0, 0, b'BOOK', b'MOBI', 3, 0, 2)
    start = len(header) + 16 + 2
    records = struct.pack('>II', start, 0) + struct.pack('>II', start + len(record0), 1)
    return header + records + b'\0\0' + record0 + encoded
