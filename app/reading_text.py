"""Conservative, local selection of text to speak. Never redact inside ordinary prose."""
import re
import csv
from pathlib import Path
from collections import Counter

FIELD_NAMES = (
    r'页眉|页脚|页码|running head|通讯作者|通信作者|通讯地址|通信地址|作者单位|作者简介|'
    r'电子邮(?:件|箱)|作者邮箱|联系邮箱|联系电话|邮政编码|邮编|'
    r'出版社|出版单位|出版发行|出版日期|出版时间|出版地|印刷单位|责任编辑|'
    r'版次|印次|印刷日期|定价|文章编号|中图分类号|文献标识码|收稿日期|修回日期|录用日期|网络出版日期|'
    r'corresponding author|correspondence(?: to| address)?|author affiliations?|'
    r'e-?mail(?: address)?|tel(?:ephone)?|fax|publisher|publication date|published by|'
    r'received date|accepted date'
)
FIELD = re.compile(rf'^(?:{FIELD_NAMES})\s*[:：]\s*(.*)$', re.I)
ADDRESS = r'[A-Za-z0-9._%+-]+@(?:[A-Za-z0-9-]+\.)+[A-Za-z]{2,63}'
CONTACT = re.compile(rf'{ADDRESS}(?:\s*[,，;；]\s*{ADDRESS})*[.;。]?')
IDENTIFIER = re.compile(
    r'(?:ISBN(?:-1[03])?|ISSN)\s*[:：]?\s*[\dXx][\dXx\s-]{6,24}[\dXx]|'
    r'(?:(?:doi\s*[:：]\s*)|https?://(?:dx\.)?doi\.org/)10\.\d{4,9}/[A-Za-z0-9._;()/:+-]+|'
    r'(?:ORCID\s*[:：]?\s*|https?://orcid\.org/)\d{4}-\d{4}-\d{4}-\d{3}[\dX]', re.I)
PAGE = re.compile(r'(?:第\s*\d+\s*页(?:\s*[/／，,]?\s*共\s*\d+\s*页)?|page\s+\d+(?:\s+(?:of|/)\s*\d+)?)', re.I)
COPYRIGHT = re.compile(r'(?:©\s*\d{4}|copyright\s*(?:©|\(c\))?\s*\d{4}|版权所有\s*[:：]).*', re.I)
HEADINGS = {'版权页', '版权信息', '出版信息', '图书在版编目（cip）数据', '通讯作者信息',
            '出版发行信息', 'copyright page', 'publication information', 'corresponding author information'}
BODY_MARKER = re.compile(r'(?<!\w)(?:abstract|methods?|results?|conclusions?)\s*[:：]|(?:摘要|目的|方法|结果|结论|讨论)\s*[:：]', re.I)
JSON_FIELD = re.compile(r'^"([^"\\]+)"\s*:\s*"([^"\\]*)"\s*,?$')


def metadata_line(line):
    """Only positively identified standalone fields are candidates for omission."""
    value = line.strip().lstrip('#*✉†‡ ').strip()
    pair = JSON_FIELD.fullmatch(value)
    if pair:
        value = pair[1] + ': ' + pair[2]
    if PAGE.fullmatch(value) or IDENTIFIER.fullmatch(value) or CONTACT.fullmatch(value):
        return ''
    if value.rstrip('：:').lower() in HEADINGS or value.lower() in {'all rights reserved.', 'all rights reserved', '版权所有，翻印必究。'}:
        return ''
    if len(value) > 250:
        return line
    field = FIELD.match(value)
    if field or COPYRIGHT.fullmatch(value):
        # Some PDFs put metadata and the first line of the abstract on one line.
        marker = BODY_MARKER.search(value, field.start(1) if field else 0)
        if marker:
            return value[marker.start():]
        label = value.split(':', 1)[0].split('：', 1)[0].strip().lower()
        if label in {'定价', '联系电话', '邮政编码', '邮编', 'fax', 'telephone', 'tel'} and field:
            if not re.fullmatch(r'[￥¥$+()（）0-9.,，\s—/–-]+(?:元|人民币)?', field[1]):
                return line
        if label in {'电子邮箱', '电子邮件', '联系邮箱', '作者邮箱', 'email', 'e-mail', 'email address'} and field:
            if field[1] and not CONTACT.fullmatch(field[1]):
                return line
        tail = (field[1] if field else value).rstrip('。.!！ ')
        if COPYRIGHT.fullmatch(value):
            tail = re.sub(r'\.?\s*all rights reserved$', '', tail, flags=re.I)
        if re.search(r'[。！？!?]|\.\s+[A-Z]', tail):
            return line
        return ''
    return line


CONTACT_DETAIL = re.compile(r'^(?:地址|单位|电话|传真|Address|Affiliation|Department)\s*[:：]\s*(.{1,140})$', re.I)
PUBLICATION_DETAIL = re.compile(r'^(?:书名|作者|译者|责任编辑|印刷|发行|装帧|开本)\s*[:：]\s*(.{1,100})$')
TABLE_BODY = {'正文', '摘要', '方法', '结果', '结论', '指标', '数值', '说明', '标题',
              'text', 'abstract', 'methods', 'results', 'value', 'title', 'description'}
TABLE_FIELD = re.compile(rf'(?:{FIELD_NAMES}|ISBN|ISSN|DOI|ORCID)', re.I)


def table_lines(content, delimiter):
    # Use a header only when it explicitly identifies both metadata and body.
    omitted = set()
    width = 0
    for line in content.splitlines():
        if not line.strip():
            omitted.clear()
            yield line
            continue
        try:
            row = next(csv.reader([line], delimiter=delimiter, strict=True))
        except csv.Error:
            omitted.clear()
            yield line
            continue
        labels = [field.strip().casefold() for field in row]
        fields = {i for i, field in enumerate(labels) if TABLE_FIELD.fullmatch(field)}
        if fields and TABLE_BODY.intersection(labels):
            width, omitted = len(row), fields
        elif len(row) != width or TABLE_BODY.intersection(labels):
            omitted.clear()
        if omitted:
            yield '，'.join(value for i, value in enumerate(row) if i not in omitted)
        elif len(row) == 2 and TABLE_FIELD.fullmatch(labels[0]) and not metadata_line(row[0] + '：' + row[1]):
            continue
        elif len(row) > 1:
            kept = [metadata_line(cell) for cell in row]
            yield line if kept == row else '，'.join(cell for cell in kept if cell)
        else:
            yield line


def spoken_text(content, filename=''):
    if '\f' in content:
        pages = content.split('\f')
        margins = []
        for page in pages:
            lines = [line.strip() for line in page.splitlines() if line.strip()]
            margins.append((set(lines[:1]), set(lines[-1:])))
        content = pdf_body(pages, margins)
    suffix = Path(filename).suffix.casefold()
    if suffix in {'.csv', '.xlsx'}:
        delimiter = '，' if suffix == '.xlsx' else ','
        lines = table_lines(content, delimiter)
    else:
        lines = content.splitlines()
    result = []
    context = None
    for line in lines:
        label = line.strip().lstrip('#*✉†‡ ').strip()
        detail = context.fullmatch(label) if context else None
        if detail and not re.search(r'[。！？!?]', detail[1].rstrip('。.!')):
            continue
        kept = metadata_line(line)
        if kept or not line.strip():
            result.append(kept)
            context = None
        elif re.match(r'(?:通讯|通信|correspond)', label, re.I):
            context = CONTACT_DETAIL
        elif label.rstrip('：:').lower() in HEADINGS - {'通讯作者信息', 'corresponding author information'}:
            context = PUBLICATION_DETAIL
    cleaned = '\n'.join(result).strip()
    return cleaned if any(c.isalnum() for c in cleaned) else ''


def edge_key(line):
    value = ' '.join(line.split()).casefold()
    if len(value) > 95 or len(value) < 4 or not any(c.isalpha() for c in value):
        return None
    if re.search(r'[。！？!?=]|[.!]$', value) or value in {
        'abstract', 'introduction', 'methods', 'results', 'discussion', 'conclusion'}:
        return None
    # Replace only edge page numbers, not years and measurements inside titles.
    return re.sub(r'(?:^\d{1,5}\s*[|｜–—-]\s*|\s*[|｜–—-]\s*\d{1,5}$)', '', value)


def pdf_body(pages, margins):
    """Use repetition at the same margin; retain the first copy of a running title."""
    if len(pages) < 3:
        return '\n'.join(pages)
    lines = [[s for s in page.splitlines() if s.strip()] for page in pages]
    removed = [set() for _ in pages]
    for top in (True, False):
        counts = Counter()
        numbers = Counter()
        edges = []
        for page_index, rows in enumerate(lines):
            if len(rows) < 3:
                edges.append([])
                continue
            margin = list(range(min(2, len(rows)//2)))
            if not top:
                margin = [len(rows)-1-i for i in margin]
            visible = margins[page_index][0 if top else 1]
            margin = [pos for pos in margin if rows[pos].strip() in visible]
            edges.append(margin)
            if not margin:
                continue
            counts.update({key for i in margin if (key := edge_key(rows[i]))})
            border = rows[margin[0]].strip()
            if re.fullmatch(r'[0-9]{1,5}', border):
                numbers[int(border)-page_index] += 1
        seen = set()
        for page_index, margin in enumerate(edges):
            for pos in margin:
                row = lines[page_index][pos].strip()
                key = edge_key(row)
                if pos == margin[0] and re.fullmatch(r'[0-9]{1,5}', row) and numbers[int(row)-page_index] >= 3:
                    removed[page_index].add(pos)
                if key and counts[key] >= 3:
                    if key in seen:
                        removed[page_index].add(pos)
                    seen.add(key)
    return '\n'.join(row for rows, skip in zip(lines, removed) for i, row in enumerate(rows) if i not in skip)
