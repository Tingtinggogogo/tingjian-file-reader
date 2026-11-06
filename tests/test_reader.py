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
os.environ['ACCESS_CODE'] = 'test-access-code-1234'
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
    main.ATTEMPTS.clear()
    c = TestClient(main.app, headers={'Origin': 'http://testserver'})
    assert c.post('/api/login', json={'code': os.environ['ACCESS_CODE']}).status_code == 200
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


def test_02_login_rate_limit(client):
    for _ in range(9):
        assert client.post('/api/login', json={'code': 'wrong'}).status_code == 401
    assert client.post('/api/login', json={'code': 'wrong'}).status_code == 429


def test_03_csrf_origin_required(client):
    assert client.put('/api/draft', json={'content': 'test'}, headers={'Origin': 'https://evil.test'}).status_code == 403
    assert TestClient(main.app).post('/api/login', json={'code': os.environ['ACCESS_CODE']}).status_code == 403


def test_04_session_isolation_and_drafts(client):
    client.put('/api/draft', json={'content': '私人中文草稿'})
    assert client.get('/api/draft').json()['content'] == '私人中文草稿'
    other = TestClient(main.app, headers={'Origin': 'http://testserver'})
    other.post('/api/login', json={'code': os.environ['ACCESS_CODE']})
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
    assert client.post('/api/speech', json={'content': ''}).status_code == 422
    assert client.post('/api/speech', json={'content': '你好', 'provider': 'edge'}).status_code == 422
    assert client.post('/api/speech', json={'content': '你好', 'voice': 'arbitrary-command'}).status_code == 422
    result = client.post('/api/speech', json={'content': '你好'})
    assert result.status_code == 202
    assert client.get('/api/jobs').json()[0]['status'] == 'queued'
    assert client.get('/api/audio/' + result.json()['id']).status_code == 404


def test_11_speech_quota(client):
    for _ in range(10):
        assert client.post('/api/speech', json={'content': '中文朗读'}).status_code == 202
    assert client.post('/api/speech', json={'content': '额外请求'}).status_code == 429


def test_12_audio_ownership_and_cleanup(client):
    job_id = client.post('/api/speech', json={'content': '音频'}).json()['id']
    path = main.DATA / (job_id + '.wav'); path.write_bytes(b'RIFF-test')
    with main.DB.begin() as db:
        db.execute(text("UPDATE jobs SET status='done' WHERE id=:id"), {'id': job_id})
    assert client.get('/api/audio/' + job_id).status_code == 200
    other = TestClient(main.app, headers={'Origin': 'http://testserver'})
    other.post('/api/login', json={'code': os.environ['ACCESS_CODE']})
    assert other.get('/api/audio/' + job_id).status_code == 404
    client.put('/api/draft', json={'content': 'clear me'})
    assert client.delete('/api/data').status_code == 200
    assert not path.exists()
    assert client.get('/api/draft').json()['content'] == ''


def test_13_expiry_recovery_and_active_delete(client):
    job_id = client.post('/api/speech', json={'content': '过期内容'}).json()['id']
    assert client.delete('/api/data').status_code == 409
    path = main.DATA / (job_id + '.wav'); path.write_bytes(b'test')
    with main.DB.begin() as db:
        db.execute(text("UPDATE jobs SET status='processing' WHERE id=:id"), {'id': job_id})
    main.initialize()
    assert client.get('/api/jobs').json()[0]['status'] == 'queued'
    with main.DB.begin() as db:
        db.execute(text("UPDATE jobs SET created=:t WHERE id=:id"), {'id': job_id, 't': time.time() - 90000})
    main.cleanup()
    assert client.get('/api/jobs').json() == []
    assert not path.exists()


def test_14_offline_real_synthesis_or_error_reporting(client, monkeypatch):
    import shutil
    import subprocess
    import threading
    job_id = client.post('/api/speech', json={'content': '你好，这是中文朗读测试。'}).json()['id']
    real_run = subprocess.run
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


def test_15_natural_voice_rate_metadata_and_preview_privacy(client, monkeypatch):
    monkeypatch.setattr(main, 'EDGE_ENABLED', True)
    client.put('/api/draft', json={'content': '不应该发送的私人草稿'})
    assert client.post('/api/speech', json={'content': '你好', 'provider': 'edge', 'consent': False}).status_code == 422
    for voice in ('female', 'xiaoyi', 'male'):
        result = client.post('/api/preview', json={'voice': voice, 'rate': 'slow', 'content': '不应该发送的额外内容'})
        assert result.status_code == 202
        with main.DB.connect() as db:
            job = db.execute(text('SELECT * FROM jobs WHERE id=:id'), result.json()).mappings().one()
            assert job['content'] == main.PREVIEW_TEXT
            assert (job['voice'], job['rate'], job['kind']) == (voice, 'slow', 'preview')
    assert client.get('/api/draft').json()['content'] == '不应该发送的私人草稿'
    assert client.get('/api/jobs').json()[0]['kind'] == 'preview'
    assert client.post('/api/preview', json={'voice': 'invalid'}).status_code == 422
    assert client.post('/api/preview', json={'rate': '+999%'}).status_code == 422
    assert TestClient(main.app, headers={'Origin': 'http://testserver'}).post('/api/preview', json={}).status_code == 401
    monkeypatch.setattr(main, 'EDGE_ENABLED', False)
    assert client.post('/api/preview', json={}).status_code == 422


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
