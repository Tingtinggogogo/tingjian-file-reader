import io
from sqlalchemy import text
from test_reader import client, main
from ebook_samples import epub_sample, mobi_sample
from app.chapters import sections
from app.ebooks import epub_text


def toc_book(ncx=False):
    chapters = {'front.xhtml': '<html><body><section epub:type="copyright-page"><p>版权信息</p></section></body></html>',
                'together.xhtml': '<html><body><div id="a"><p>甲章正文。</p></div><div id="b"><p>乙章开始。</p></div></body></html>',
                'more.xhtml': '<p>乙章继续。</p>'}
    items = ''.join(f'<item id="c{i}" href="{name}" media-type="application/xhtml+xml"/>' for i,name in enumerate(chapters))
    refs = ''.join(f'<itemref idref="c{i}"/>' for i in range(len(chapters)))
    if ncx:
        items += '<item id="toc" href="toc.ncx" media-type="application/x-dtbncx+xml"/>'
        nav = '<ncx><navMap>'+''.join(f'<navPoint><navLabel><text>{name}</text></navLabel><content src="together.xhtml#{mark}"/></navPoint>' for name,mark in [('海边','a'),('归途','b')])+'</navMap></ncx>'
        file = 'Books/toc.ncx'
    else:
        items += '<item id="toc" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>'
        nav = '<html xmlns:epub="http://www.idpf.org/2007/ops"><nav epub:type="toc"><ol><li><a href="together.xhtml#a">海边</a></li><li><a href="together.xhtml#b">归途</a></li></ol></nav></html>'
        file = 'Books/nav.xhtml'
    return epub_sample(chapters=chapters, extra={'Books/package.opf':f'<package><manifest>{items}</manifest><spine toc="toc">{refs}</spine></package>',file:nav})


def test_toc_fragments_and_spine_continuation():
    for ncx in [False,True]:
        marks = []
        content = epub_text(toc_book(ncx),marks)
        assert [(title,body.strip()) for title,body in sections(content,marks)] == [('海边','甲章正文。'),('归途','乙章开始。\n乙章继续。')]


def test_upload_to_chapter_jobs_and_audio_filename(client):
    uploaded = client.post('/api/extract',files={'file':('小书.epub',toc_book())}).json()
    assert uploaded['chapter_count']==2
    payload={'content':uploaded['content'],'title':'小书.epub','consent':True}
    # Metadata survives refresh and the old UI's short-file /speech route.
    client.put('/api/draft',json={'content':uploaded['content'],'filename':'小书.epub'})
    queued=client.post('/api/speech',json=payload).json()
    assert queued['chaptered'] and queued['parts']==2
    jobs=client.get('/api/jobs').json()
    assert [(j['chapter_title'],j['status']) for j in jobs]==[('海边','queued'),('归途','scheduled')]
    with main.DB.begin() as db:
        db.execute(text("UPDATE jobs SET status='done' WHERE id=:i"),{'i':jobs[0]['id']})
    (main.DATA/(jobs[0]['id']+'.mp3')).write_bytes(b'test')
    from urllib.parse import unquote
    assert '海边' in unquote(client.get('/api/audio/'+jobs[0]['id']).headers['content-disposition'])
    assert client.delete('/api/books/'+queued['book_id']).status_code==200


def test_word_heading_hierarchy_and_content_order(client):
    from docx import Document
    document=Document()
    document.add_heading('样本来源',level=1)
    document.add_paragraph('样本一。')
    document.add_heading('纳入条件',level=2)
    document.add_table(rows=1,cols=1).cell(0,0).text='表格中的条件。'
    document.add_heading('测量流程',level=1)
    document.add_paragraph('样本二。')
    stream=io.BytesIO();document.save(stream)
    data=client.post('/api/extract',files={'file':('研究.docx',stream.getvalue())}).json()
    assert data['chapter_count']==2
    response=client.post('/api/speech',json={'content':data['content'],'title':'研究.docx','consent':True}).json()
    with main.DB.connect() as db:
        rows=db.execute(text('SELECT chapter_title,content FROM jobs WHERE book_id=:b ORDER BY part_no'),{'b':response['book_id']}).all()
    assert rows==[('样本来源','样本来源\n样本一。\n纳入条件\n表格中的条件。'),('测量流程','测量流程\n样本二。')]


def test_pdf_bookmarks_same_page(client):
    from pypdf import PdfWriter
    from pypdf.generic import DictionaryObject,NameObject,DecodedStreamObject
    writer=PdfWriter();page=writer.add_blank_page(300,400)
    font=writer._add_object(DictionaryObject({NameObject('/Type'):NameObject('/Font'),NameObject('/Subtype'):NameObject('/Type1'),NameObject('/BaseFont'):NameObject('/Helvetica')}))
    page[NameObject('/Resources')]=DictionaryObject({NameObject('/Font'):DictionaryObject({NameObject('/F1'):font})})
    stream=DecodedStreamObject()
    stream.set_data(b'BT /F1 10 Tf 15 350 Td (The Pond) Tj 0 -30 Td (Water.) Tj 0 -40 Td (The Forest) Tj 0 -20 Td (Trees.) Tj ET')
    page[NameObject('/Contents')]=writer._add_object(stream)
    writer.add_outline_item('The Pond',0);writer.add_outline_item('The Forest',0)
    out=io.BytesIO();writer.write(out)
    doc=client.post('/api/extract',files={'file':('paper.pdf',out.getvalue())}).json()
    assert doc['chapter_count']==2
    response=client.post('/api/speech',json={'content':doc['content'],'title':'paper.pdf','consent':True}).json()
    assert response['parts']==2 and response['chaptered']
    assert [j['chapter_title'] for j in client.get('/api/jobs').json()]==['The Pond','The Forest']


def test_private_outline_unavailable_for_other_browser(client):
    from fastapi.testclient import TestClient
    doc=client.post('/api/extract',files={'file':('小书.epub',toc_book())}).json()
    other=TestClient(main.app,headers={'Origin':'http://testserver'})
    other.post('/api/session',json={})
    response=other.post('/api/books',json={'content':doc['content'],'title':'小书.epub','consent':True}).json()
    assert response['parts']==1 and not response['chaptered']


def test_long_chapter_keeps_one_output():
    from app.tts import synthesis_parts,speech_timeout
    chapter=('足够长的中文句子。'*2000)
    pieces=synthesis_parts(chapter)
    assert ''.join(pieces)==chapter and len(pieces)>3 and max(map(len,pieces))<=1800
    assert speech_timeout(len(chapter))>180


def test_real_mobi_chapter_headings(client):
    data=mobi_sample(html_content='<html><body><h1>第一章 海</h1><p>海水是蓝的。</p><h1>第二章 山</h1><p>山路向北。</p></body></html>')
    result=client.post('/api/extract',files={'file':('实测.mobi',data)})
    assert result.status_code==200, result.text
    chapter=client.post('/api/books',json={'content':result.json()['content'],'title':'实测.mobi','consent':True})
    assert chapter.status_code==202 and chapter.json()['chaptered']
    assert [j['chapter_title'] for j in client.get('/api/jobs').json()]==['第一章 海','第二章 山']


def test_later_chapter_waits_for_quota_without_losing_order(client):
    import time
    response=client.post('/api/books',json={'content':'第一章 海\n蓝色海水。\n第二章 山\n绿色山林。','title':'测试.epub','consent':True}).json()
    book_id=response['book_id']
    jobs=client.get('/api/jobs').json()
    client.delete('/api/jobs/'+jobs[0]['id'])
    with main.DB.begin() as db:
        db.execute(text("UPDATE usage_limits SET count=100 WHERE bucket LIKE 'speech-hour:%'"))
    main.STOP.clear()
    main.run_next()
    with main.DB.connect() as db:
        row=db.execute(text('SELECT * FROM jobs WHERE book_id=:b'),{'b':book_id}).mappings().one()
        assert row['status']=='queued' and row['budget_due']==1 and row['ready_at']>time.time()
