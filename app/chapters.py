"""Chapter boundaries refer to offsets in the extracted text, before narration filtering."""
import re

CHAPTER = re.compile(r'^(?:第\s*[〇零一二两三四五六七八九十百千万\d]+\s*[章回]|chapter\s+(?:\d+|[ivxlcdm]+|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|twenty|thirty|forty|fifty)\b)', re.I)
SECTION = re.compile(r'^(?:[1-9]\d?[.、．]?\s+|[一二三四五六七八九十]+[、．]\s*|[IVX]{1,5}\.\s+)\S')
PAPER = {'摘要','引言','绪论','背景','研究方法','材料与方法','方法','结果','讨论','结论','附录',
         '相关工作','实验设计','文献综述','总结','related work','literature review','experiments','methodology',
         'abstract','introduction','background','materials and methods','methods','results','discussion','conclusion','conclusions','appendix'}


def text_marks(content):
    marks, offset = [], 0
    for line in content.splitlines(keepends=True):
        title = line.strip()
        heading = title.lstrip('#').strip()
        comparable = heading.rstrip('。.!！') if CHAPTER.match(heading) else heading
        if (0 < len(heading) <= 96 and not re.search(r'[。！？!?]|(?:\.{3,}|…+)\s*\d+$', comparable)
                and (CHAPTER.match(heading) or SECTION.match(heading)
                     or heading.casefold() in PAPER or title.startswith('# '))):
            marks.append((offset, heading))
        offset += len(line)
    chapters = [mark for mark in marks if CHAPTER.match(mark[1])]
    return chapters or marks


def sections(content, marks):
    """Preserve the prefix, every body character, and the original reading order."""
    ordered = sorted({int(pos): str(title)[:160] for pos,title in marks
                      if 0 <= pos < len(content) and str(title).strip()}.items())
    if not ordered:
        return []
    if ordered[0][0] > 0:
        ordered.insert(0,(0,'章前内容'))
    ends = [pos for pos,_ in ordered[1:]] + [len(content)]
    output = []
    prefix = ''
    for (start,title),end in zip(ordered,ends):
        body = content[start:end]
        if end < len(content) and (body.strip() == title or
                (title == '章前内容' and len(body.strip()) <= 160 and '\n' not in body.strip())):
            prefix += body
            continue
        output.append((title,prefix + body))
        prefix = ''
    return output


def primary(entries):
    """Keep top-level entries plus explicitly named chapters nested under volumes."""
    if not entries:
        return []
    level = min(entry[0] for entry in entries)
    roots = [e for e in entries if e[0] == level]
    deeper = [e for e in entries if e[0] > level]
    if len(roots) == 1 and deeper and not CHAPTER.match(roots[0][1]):
        level = min(e[0] for e in deeper)
    result = []
    for i,entry in enumerate(entries):
        depth,title,_ = entry
        child = entries[i+1] if i+1 < len(entries) and entries[i+1][0] > depth else None
        volume = child and CHAPTER.match(child[1]) and not CHAPTER.match(title)
        if CHAPTER.match(title) or (depth <= level and not volume):
            result.append(entry)
    return result
