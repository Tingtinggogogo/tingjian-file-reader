import io
import os
import tempfile
import time
import uuid
import zipfile
from pathlib import Path

os.environ['DATA_DIR'] = tempfile.mkdtemp(prefix='reader-test-')
os.environ.setdefault('DATABASE_URL', 'sqlite:///' + os.environ['DATA_DIR'] + '/test.db')
os.environ['SESSION_SECRET'] = 'test-session-secret-that-is-long-enough'
os.environ['ENABLE_EDGE_TTS'] = 'true'
os.environ['COOKIE_SECURE'] = 'false'
os.environ['ALLOWED_ORIGINS'] = 'http://testserver'

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from app import main
from app.extract import extract


@pytest.fixture
def client():
    main.initialize()
    with main.DB.begin() as db:
        db.execute(text('DELETE FROM jobs'))
        db.execute(text('DELETE FROM drafts'))
        db.execute(text('DELETE FROM usage_limits'))
    c = TestClient(main.app, headers={'Origin': 'http://testserver'})
    assert c.post('/api/session', json={}).status_code == 200
    yield c
    c.close()


def test_01_authentication_and_secure_headers(client):
    c = TestClient(main.app)
    assert c.get('/api/draft').status_code == 401
    assert client.get('/api/health').json()['status'] == 'ok'
    response = client.get('/')
    assert response.status_code == 200
    assert "script-src 'self'" in response.headers['content-security-policy']
    assert response.headers['x-content-type-options'] == 'nosniff'


def test_02_anonymous_identity_persists_and_rejects_forgery(client):
    client.put('/api/draft', json={'content': '记住我', 'consent': True})
    response = client.post('/api/session', json={})
    assert response.status_code == 200
    assert 'HttpOnly' in response.headers['set-cookie']
    assert 'SameSite=strict' in response.headers['set-cookie']
    assert client.get('/api/draft').json()['content'] == '记住我'
    client.cookies.clear(); client.cookies.set('reader_session', 'forged')
    assert client.get('/api/draft').status_code == 401
    assert client.post('/api/session', json={}).status_code == 200
    assert client.get('/api/draft').json()['content'] == ''


def test_03_csrf_origin_required(client):
    assert client.put('/api/draft', json={'content': 'test'}, headers={'Origin': 'https://evil.test'}).status_code == 403
    assert TestClient(main.app).post('/api/session', json={}).status_code == 403


def test_04_session_isolation_and_drafts(client):
    client.put('/api/draft', json={'content': '私人中文草稿', 'consent': True})
    assert client.get('/api/draft').json()['content'] == '私人中文草稿'
    other = TestClient(main.app, headers={'Origin': 'http://testserver'})
    other.post('/api/session', json={})
    assert other.get('/api/draft').json()['content'] == ''


def test_05_text_encoding_and_unsupported_binary():
    assert extract('中文文件'.encode('gb18030'), 'read.txt') == '中文文件'
    assert extract('你好'.encode('utf-8-sig'), 'read.md') == '你好'
    with pytest.raises(ValueError):
        extract(b'MZ executable', 'virus.exe')
    with pytest.raises(ValueError):
        extract(b'\x00\x01', 'fake.txt')


def test_06_upload_and_validation(client):
    result = client.post('/api/extract', files={'file': ('test.txt', '阅读工具'.encode(), 'text/plain')})
    assert result.status_code == 200
    assert result.json()['content'] == '阅读工具'
    assert client.post('/api/extract', files={'file': ('x.exe', b'no')}).status_code == 415
    assert client.post('/api/extract', files={'file': ('x.txt', b'')}).status_code == 422
    assert client.post('/api/extract', files={'file': ('x.pdf', b'not a pdf')}).status_code == 422


def test_07_upload_size_and_text_limit(client):
    assert client.post('/api/extract', files={'file': ('x.txt', b'x' * (20 * 1024 * 1024 + 1))}).status_code == 413
    assert client.put('/api/draft', json={'content': '中' * 30001}).status_code == 422
    with pytest.raises(ValueError):
        extract(b'x' * 30001, 'x.txt')


def test_08_document_formats():
    from docx import Document
    from openpyxl import Workbook
    from pptx import Presentation
    stream = io.BytesIO(); doc = Document(); doc.add_paragraph('中文文档'); doc.save(stream)
    assert '中文文档' in extract(stream.getvalue(), 'x.docx')
    stream = io.BytesIO(); book = Workbook(); book.active.append(['中文表格', 100]); book.save(stream)
    assert '中文表格，100' in extract(stream.getvalue(), 'x.xlsx')
    stream = io.BytesIO(); deck = Presentation(); slide = deck.slides.add_slide(deck.slide_layouts[1]); slide.shapes.title.text = '中文演示'; deck.save(stream)
    assert '中文演示' in extract(stream.getvalue(), 'x.pptx')


def test_09_archive_bomb_and_empty_pdf():
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        for index in range(3001):
            archive.writestr(str(index), 'x')
    with pytest.raises(ValueError, match='解压后过大'):
        extract(stream.getvalue(), 'x.docx')
    from pypdf import PdfWriter
    stream = io.BytesIO(); pdf = PdfWriter(); pdf.add_blank_page(width=100, height=100); pdf.write(stream)
    with pytest.raises(ValueError, match='没有提取到文字'):
        extract(stream.getvalue(), 'x.pdf')


def test_10_speech_consent_validation_and_queue(client):
    assert client.post('/api/speech', json={'content': '', 'consent': True}).status_code == 422
    assert client.post('/api/speech', json={'content': '你好'}).status_code == 422
    assert client.post('/api/speech', json={'content': '你好', 'voice': 'arbitrary-command'}).status_code == 422
    result = client.post('/api/speech', json={'content': '你好', 'consent': True})
    assert result.status_code == 202
    assert client.get('/api/jobs').json()[0]['status'] == 'queued'
    assert client.get('/api/audio/' + result.json()['id']).status_code == 404


def test_11_speech_quota(client):
    for _ in range(10):
        assert client.post('/api/speech', json={'content': '中文朗读', 'consent': True}).status_code == 202
    assert client.post('/api/speech', json={'content': '额外请求', 'consent': True}).status_code == 429


def test_12_audio_ownership_and_cleanup(client):
    job_id = client.post('/api/speech', json={'content': '音频', 'consent': True}).json()['id']
    path = main.DATA / (job_id + '.mp3'); path.write_bytes(b'RIFF-test')
    with main.DB.begin() as db:
        db.execute(text("UPDATE jobs SET status='done' WHERE id=:id"), {'id': job_id})
    assert client.get('/api/audio/' + job_id).status_code == 200
    other = TestClient(main.app, headers={'Origin': 'http://testserver'})
    other.post('/api/session', json={})
    assert other.get('/api/audio/' + job_id).status_code == 404
    client.put('/api/draft', json={'content': 'clear me', 'consent': True})
    assert client.delete('/api/data').status_code == 200
    assert not path.exists()
    assert client.get('/api/draft').json()['content'] == ''


def test_13_expiry_recovery_and_active_delete(client):
    job_id = client.post('/api/speech', json={'content': '过期内容', 'consent': True}).json()['id']
    path = main.DATA / (job_id + '.mp3'); path.write_bytes(b'test')
    with main.DB.begin() as db:
        db.execute(text("UPDATE jobs SET status='processing' WHERE id=:id"), {'id': job_id})
    main.initialize()
    assert client.get('/api/jobs').json()[0]['status'] == 'queued'
    with main.DB.begin() as db:
        db.execute(text("UPDATE jobs SET created=:t WHERE id=:id"), {'id': job_id, 't': time.time() - 31 * 86400})
    main.cleanup()
    assert client.get('/api/jobs').json() == []
    assert not path.exists()


def test_14_offline_real_synthesis_or_error_reporting(client, monkeypatch):
    import shutil
    import subprocess
    import threading
    job_id = client.post('/api/speech', json={'content': '你好，这是中文朗读测试。', 'consent': True}).json()['id']
    with main.DB.begin() as db:
        db.execute(text("UPDATE jobs SET provider='offline' WHERE id=:id"), {'id': job_id})
    def unavailable(*args, **kwargs):
        raise subprocess.CalledProcessError(1, ['espeak-ng'])
    installed = shutil.which('espeak-ng') is not None or (main.sys.platform == 'darwin' and Path('/usr/bin/say').exists())
    if not installed:
        monkeypatch.setattr(main.subprocess, 'run', unavailable)
    main.STOP.clear(); thread = threading.Thread(target=main.work, daemon=True); thread.start()
    try:
        deadline = time.time() + 20
        while time.time() < deadline:
            job = client.get('/api/jobs').json()[0]
            if job['status'] in ('done', 'failed'):
                break
            time.sleep(.2)
        assert job['status'] == ('done' if installed else 'failed')
        if installed:
            response = client.get('/api/audio/' + job_id)
            assert response.content[:4] == b'RIFF'
            assert len(response.content) > 44
        else:
            assert job['error']
    finally:
        main.STOP.set(); thread.join(3)


def test_15_only_xiaoxiao_can_be_requested_with_explicit_consent(client, monkeypatch):
    assert client.post('/api/speech', json={'content': '你好'}).status_code == 422
    for extra in ({'voice': 'male'}, {'provider': 'offline'}, {'rate': 'fast'}):
        assert client.post('/api/speech', json={'content': '你好', 'consent': True, **extra}).status_code == 422
    result = client.post('/api/speech', json={'content': '你好', 'title': '我的文件.txt', 'consent': True})
    assert result.status_code == 202
    with main.DB.connect() as db:
        job = db.execute(text('SELECT * FROM jobs WHERE id=:id'), result.json()).mappings().one()
        assert (job['voice'], job['provider'], job['rate'], job['title']) == ('female', 'edge', 'slow', '我的文件.txt')
    monkeypatch.setattr(main, 'EDGE_ENABLED', False)
    assert client.post('/api/speech', json={'content': '你好', 'consent': True}).status_code == 422


def test_16_additive_migration_preserves_existing_jobs_and_drafts(tmp_path, monkeypatch):
    from sqlalchemy import create_engine
    database = create_engine('sqlite:///' + str(tmp_path / 'legacy.db'))
    monkeypatch.setattr(main, 'DB', database)
    with database.begin() as db:
        for statement in main.SCHEMA:
            db.execute(text(statement))
        db.execute(text("INSERT INTO jobs VALUES ('legacy','owner','旧文字','female','offline','done','',1)"))
        db.execute(text("INSERT INTO drafts VALUES ('owner','保留草稿',1)"))
    main.initialize(); main.initialize()
    with database.connect() as db:
        row = db.execute(text('SELECT * FROM jobs')).mappings().one()
        assert (row['content'], row['status'], row['rate'], row['kind']) == ('旧文字', 'done', 'normal', 'reading')
        assert db.execute(text('SELECT content FROM drafts')).scalar() == '保留草稿'
    database.dispose()


def test_17_edge_voice_and_rate_forwarded_without_text_fragmentation(tmp_path, monkeypatch):
    import edge_tts
    from app.tts import synthesize
    calls = []
    class FakeSpeech:
        def __init__(self, content, voice, *, rate):
            calls.append((content, voice, rate))
        async def save(self, target):
            Path(target).write_bytes(b'mock-audio')
    monkeypatch.setattr(edge_tts, 'Communicate', FakeSpeech)
    content = '你好，欢迎使用。我们慢慢来。'
    for voice, expected in [('female', 'zh-CN-XiaoxiaoNeural'), ('xiaoyi', 'zh-CN-XiaoyiNeural'), ('male', 'zh-CN-YunxiNeural')]:
        synthesize(content, voice, 'edge', tmp_path / 'test.mp3', 'slow')
        assert calls[-1] == (content, expected, '-15%')
    assert len(calls) == 3
    with pytest.raises(ValueError):
        synthesize(content, 'female', 'edge', tmp_path / 'test.mp3', 'invalid')


def test_18_global_budget_survives_new_browser_and_job_deletion(client):
    with main.DB.begin() as db:
        db.execute(text('INSERT INTO usage_limits(bucket,count,created) VALUES(:b,100,:t)'),
                   {'b': f'speech-hour:{int(time.time() // 3600)}', 't': time.time()})
    assert client.delete('/api/data').status_code == 200
    client.cookies.clear(); client.post('/api/session', json={})
    assert client.post('/api/speech', json={'content': '你好', 'consent': True}).status_code == 429
    assert client.get('/api/jobs').json() == []


def test_19_history_retention_pagination_and_isolation(client):
    first = client.post('/api/speech', json={'content': '你好', 'consent': True}).json()['id']
    with main.DB.begin() as db:
        who = db.execute(text('SELECT owner FROM jobs WHERE id=:id'), {'id': first}).scalar_one()
        db.execute(text("UPDATE jobs SET status='done',created=:t WHERE id=:id"), {'t': time.time() - 2 * 86400, 'id': first})
        for index in range(25):
            db.execute(text("""INSERT INTO jobs(id,owner,content,voice,provider,status,error,created)
                VALUES(:id,:o,'','female','edge','done','',:t)"""), {'id': str(uuid.uuid4()), 'o': who, 't': time.time() - index})
    main.cleanup()
    assert len(client.get('/api/jobs').json()) == 20
    second = client.get('/api/jobs?offset=20').json()
    assert len(second) == 6 and second[-1]['id'] == first
    assert client.get('/api/jobs?offset=-1').status_code == 422
    other = TestClient(main.app, headers={'Origin': 'http://testserver'})
    other.post('/api/session', json={})
    assert other.get('/api/jobs').json() == []


def test_20_storage_and_upload_budgets(client, monkeypatch):
    monkeypatch.setattr(main, 'MAX_AUDIO_BYTES', 0)
    assert client.post('/api/speech', json={'content': '你好', 'consent': True}).status_code == 503
    monkeypatch.setattr(main, 'MAX_AUDIO_BYTES', 5 * 1024 ** 3)
    with main.DB.begin() as db:
        db.execute(text('INSERT INTO usage_limits(bucket,count,created) VALUES(:b,120,:t)'),
                   {'b': f'uploads-hour:{int(time.time() // 3600)}', 't': time.time()})
    assert client.post('/api/extract', files={'file': ('test.txt', '你好'.encode())}).status_code == 429


def test_21_epub_reading_order_and_safe_html(client):
    from ebook_samples import epub_sample
    data = epub_sample()
    result = extract(data, '书籍.EPUB')
    assert result == '第一章\n你好，世界。\n第二章：晚安。'
    assert client.post('/api/extract', files={'file': ('中文.epub', data, 'application/epub+zip')}).json()['content'] == result
    session = client.post('/api/session', json={}).json()
    assert {'.epub', '.mobi'}.issubset(session['extensions'])


@pytest.mark.parametrize('kind', ['drm', 'traversal', 'remote', 'xml-entity', 'missing', 'oversized', 'empty', 'invalid'])
def test_22_epub_rejects_unreadable_or_unsafe_books(kind):
    from ebook_samples import epub_sample
    extra, chapters = {}, None
    if kind == 'drm':
        extra['META-INF/encryption.xml'] = '<encryption><EncryptedData><CipherData><CipherReference URI="Books/first.xhtml"/></CipherData></EncryptedData></encryption>'
    elif kind == 'traversal':
        chapters = {'../../outside.xhtml': '<p>拒绝越界</p>'}
    elif kind == 'remote':
        chapters = {'https://example.invalid/private.xhtml': '<p>不访问网络</p>'}
    elif kind == 'xml-entity':
        extra['META-INF/container.xml'] = '<!DOCTYPE container [<!ENTITY x SYSTEM "file:///etc/passwd">]><container>&x;</container>'
    elif kind == 'missing':
        extra['Books/package.opf'] = '<package><manifest/><spine><itemref idref="missing"/></spine></package>'
    elif kind == 'oversized':
        chapters = {'long.xhtml': '<p>' + '中' * 300001 + '</p>'}
    elif kind == 'empty':
        chapters = {'image.xhtml': '<html><body><img src="cover.png"/></body></html>'}
    elif kind == 'invalid':
        with pytest.raises(ValueError, match='损坏'):
            extract(b'not a zip', 'fake.epub')
        return
    with pytest.raises(ValueError):
        extract(epub_sample(chapters=chapters, extra=extra), 'bad.epub')


def test_23_epub_obfuscated_fonts_and_archive_limits():
    from ebook_samples import epub_sample
    font = {'META-INF/encryption.xml': '<encryption><EncryptedData><CipherData><CipherReference URI="Books/font.otf"/></CipherData></EncryptedData></encryption>'}
    assert '第一章' in extract(epub_sample(extra=font), 'font.epub')
    with pytest.raises(ValueError, match='解压后过大'):
        extract(epub_sample(extra={f'padding/{i}': '' for i in range(3001)}), 'many.epub')


@pytest.mark.parametrize('compressed', [False, True])
def test_24_mobi_actual_decoder_and_upload(client, compressed):
    import shutil
    from ebook_samples import mobi_sample
    if not shutil.which('mobitool'):
        if os.environ.get('CI'):
            pytest.fail('CI must install libmobi-tools for real MOBI verification')
        pytest.skip('Local MOBI test requires mobitool; required in CI')
    data = mobi_sample(compressed=compressed)
    result = extract(data, 'book.mobi')
    assert result == '第一章\n你好，欢迎使用听见文件。\n第二章，晚安。'
    response = client.post('/api/extract', files={'file': ('中文.mobi', data, 'application/x-mobipocket-ebook')})
    assert response.status_code == 200 and response.json()['content'] == result


def test_25_mobi_drm_corruption_and_expansion_limit(client):
    import struct
    from ebook_samples import mobi_sample
    with pytest.raises(ValueError, match='DRM'):
        extract(mobi_sample(encrypted=True), 'locked.mobi')
    with pytest.raises(ValueError, match='格式不符'):
        extract(b'fake mobi', 'fake.mobi')
    data = bytearray(mobi_sample()); header = struct.unpack_from('>I', data, 78)[0]
    struct.pack_into('>I', data, header + 4, 256 * 1024 * 1024 + 1)
    with pytest.raises(ValueError, match='解压后过大'):
        extract(bytes(data), 'bomb.mobi')
    struct.pack_into('>I', data, 78, 0)
    with pytest.raises(ValueError, match='损坏'):
        extract(bytes(data), 'broken.mobi')
    response = client.post('/api/extract', files={'file': ('locked.mobi', mobi_sample(encrypted=True))})
    assert response.status_code == 422 and 'DRM' in response.json()['detail']


def test_26_mobi_timeout_cleans_temporary_files(tmp_path, monkeypatch):
    from app import ebooks
    from ebook_samples import mobi_sample
    def timeout(*args, **kwargs):
        raise ebooks.subprocess.TimeoutExpired('mobitool', 45)
    monkeypatch.setattr(ebooks.shutil, 'which', lambda name: '/test/mobitool')
    monkeypatch.setattr(ebooks.subprocess, 'run', timeout)
    with pytest.raises(ValueError, match='超时'):
        extract(mobi_sample(), 'timeout.mobi', tmp_path)
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize('status', ['queued', 'failed', 'done'])
def test_delete_one_private_job_in_any_state(client, status):
    job = client.post('/api/speech', json={'content': '测试', 'consent': True}).json()['id']
    other_job = client.post('/api/speech', json={'content': '保留', 'consent': True}).json()['id']
    with main.DB.begin() as db:
        db.execute(text('UPDATE jobs SET status=:s WHERE id=:id'), {'s': status, 'id': job})
    (main.DATA / (job + '.mp3')).write_bytes(b'private')
    outsider = TestClient(main.app, headers={'Origin': 'http://testserver'})
    outsider.post('/api/session', json={})
    assert outsider.delete('/api/jobs/' + job).status_code == 404
    assert client.delete('/api/jobs/' + job, headers={'Origin': 'https://bad.test'}).status_code == 403
    assert client.delete('/api/jobs/' + job).status_code == 200
    assert [j['id'] for j in client.get('/api/jobs').json()] == [other_job]
    assert client.get('/api/audio/' + job).status_code == 404
    assert not (main.DATA / (job + '.mp3')).exists()


@pytest.mark.parametrize('bulk', [False, True])
def test_cancel_running_process_and_no_late_audio(client, monkeypatch, bulk):
    import threading
    import subprocess
    job_id = client.post('/api/speech', json={'content': '取消测试', 'consent': True}).json()['id']
    started = threading.Event()
    children = []
    def launch(job):
        directory = main.workdir(job['id'])
        directory.mkdir()
        target = directory / 'audio.mp3'
        target.write_bytes(b'unpublished')
        proc = subprocess.Popen([main.sys.executable, '-c', 'import time; time.sleep(60)'], start_new_session=True)
        children.append(proc)
        started.set()
        return proc, target
    monkeypatch.setattr(main, 'launch', launch)
    main.STOP.clear()
    worker = threading.Thread(target=main.run_next)
    worker.start()
    try:
        assert started.wait(5)
        assert client.get('/api/jobs').json()[0]['status'] == 'processing'
        assert client.delete('/api/data' if bulk else '/api/jobs/' + job_id).status_code == 200
        worker.join(5)
        assert not worker.is_alive() and children[0].poll() is not None
        assert client.get('/api/jobs').json() == []
        assert not main.workdir(job_id).exists()
        assert not (main.DATA / (job_id + '.mp3')).exists()
        main.initialize()
        assert client.get('/api/audio/' + job_id).status_code == 404
    finally:
        for proc in children:
            main.stop_process(proc)
        worker.join(5)


def test_deleting_job_preserves_hourly_allowance(client):
    for _ in range(10):
        job = client.post('/api/speech', json={'content': '公开测试', 'consent': True})
        assert job.status_code == 202
        assert client.delete('/api/jobs/' + job.json()['id']).status_code == 200
    assert client.post('/api/speech', json={'content': '公开测试', 'consent': True}).status_code == 429


def test_long_book_order_advancement_restart_and_private_removal(client):
    content = '这一段是中文测试。' * 7300
    response = client.post('/api/books', json={'content': content, 'title': '长篇.epub', 'consent': True})
    assert response.status_code == 202
    book_id = response.json()['book_id']
    rows = client.get('/api/jobs').json()
    assert 1 < len(rows) == response.json()['parts']
    assert [j['part_no'] for j in rows] == list(range(1, len(rows)+1))
    assert [j['status'] for j in rows] == ['queued'] + ['scheduled']*(len(rows)-1)
    with main.DB.connect() as db:
        parts = db.execute(text('SELECT content FROM jobs WHERE book_id=:b ORDER BY part_no'), {'b': book_id}).scalars().all()
        assert ''.join(parts) == content and max(map(len, parts)) <= 5000
    assert client.post('/api/books', json={'content': content, 'title': '另一本.mobi', 'consent': True}).status_code == 429
    main.initialize()
    assert client.delete('/api/jobs/' + rows[0]['id']).status_code == 200
    assert client.get('/api/jobs').json()[0]['status'] == 'queued'
    with main.DB.begin() as db:
        db.execute(text("UPDATE jobs SET status='done' WHERE id=:id"), {'id': rows[1]['id']})
        main.advance_book(db, book_id)
    assert client.get('/api/jobs').json()[1]['status'] == 'queued'
    outsider = TestClient(main.app, headers={'Origin': 'http://testserver'})
    outsider.post('/api/session', json={})
    assert outsider.delete('/api/books/' + book_id).status_code == 404
    assert client.delete('/api/books/' + book_id).status_code == 200
    assert client.get('/api/jobs').json() == []


def test_large_ebook_upload_and_draft(client):
    from ebook_samples import epub_sample
    # A valid ebook with an incompressible illustration larger than 30 MiB.
    content = '大书测试。' * 8000
    data = epub_sample(chapters={'book.xhtml': '<p>'+content+'</p>'}, extra={'Books/art.bin': os.urandom(31 * 1024**2)})
    assert 30 * 1024**2 < len(data) < main.EBOOK_UPLOAD
    uploaded = client.post('/api/extract', files={'file': ('大书.epub', data)})
    assert uploaded.status_code == 200 and uploaded.json()['content'] == content
    assert client.put('/api/draft', json={'content': content, 'filename': '大书.epub'}).status_code == 200
    assert client.get('/api/draft').json() == {'content': content, 'filename': '大书.epub'}
    assert len(extract(epub_sample(chapters={'full.xhtml': '<p>' + '中'*300000 + '</p>'}), '满额.epub')) == 300000
    assert client.post('/api/books', json={'content': '中' * 300001, 'title': '大书.epub', 'consent': True}).status_code == 422


def test_parallel_parts_preserve_audio_order(tmp_path, monkeypatch):
    import asyncio
    import array
    import shutil
    import subprocess
    import edge_tts
    from app.tts import split_text, synthesize
    assert shutil.which('ffmpeg'), 'Install FFmpeg to test speech assembly'
    content = '第一句。' * 30 + '第二句。' * 30 + '第三句。' * 30
    parts = split_text(content)
    assert ''.join(parts) == content and len(parts) == 3
    for i, hz in enumerate([300, 600, 900]):
        subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i', f'sine=frequency={hz}:duration=0.5',
                        '-ar', '24000', '-ac', '1', '-b:a', '48k', str(tmp_path/f'tone{i}.mp3')], check=True)
    started, finished = [], []
    class Speech:
        def __init__(self, chunk, voice, *, rate):
            self.index = parts.index(chunk)
        async def save(self, filename):
            started.append(self.index)
            while len(started) < 3:
                await asyncio.sleep(.001)
            await asyncio.sleep(.02 * (2-self.index))
            shutil.copyfile(tmp_path/f'tone{self.index}.mp3', filename)
            finished.append(self.index)
    monkeypatch.setattr(edge_tts, 'Communicate', Speech)
    output = tmp_path/'book.mp3'
    synthesize(content, 'female', 'edge', output, 'slow')
    assert finished == [2, 1, 0]
    pcm = subprocess.check_output(['ffmpeg', '-v', 'error', '-i', str(output), '-f', 's16le', '-ac', '1', '-ar', '24000', '-'])
    samples = array.array('h', pcm)
    for i, frequency in enumerate([300, 600, 900]):
        start = int((i * .55 + .2) * 24000)
        window = samples[start:start+2400]
        crossings = sum(a < 0 <= b for a,b in zip(window, window[1:]))
        assert abs(crossings - frequency / 10) <= 2
    assert not list(tmp_path.glob('part-*.mp3'))
