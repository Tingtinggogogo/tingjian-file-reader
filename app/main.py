import hashlib
import hmac
import os
import secrets
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired
from pydantic import BaseModel, Field
from sqlalchemy import create_engine, inspect, text

DATA = Path(os.getenv('DATA_DIR', 'data')).resolve()
DATA.mkdir(parents=True, exist_ok=True)
SECRET = os.getenv('SESSION_SECRET', '')
ACCESS = os.getenv('ACCESS_CODE', '')
if len(SECRET) < 32 or len(ACCESS) < 12 or SECRET.startswith('REPLACE_') or ACCESS.startswith('REPLACE_'):
    raise RuntimeError('SESSION_SECRET 至少 32 字符，ACCESS_CODE 至少 12 字符；请配置 .env')
SIGNER = URLSafeTimedSerializer(SECRET)
DB = create_engine(os.getenv('DATABASE_URL', 'sqlite:///' + str(DATA / 'reader.db')), pool_pre_ping=True)
ORIGINS = set(os.getenv('ALLOWED_ORIGINS', 'http://localhost:8000,http://127.0.0.1:8000').split(','))
COOKIE_SECURE = os.getenv('COOKIE_SECURE', 'true') == 'true'
EDGE_ENABLED = os.getenv('ENABLE_EDGE_TTS', 'false') == 'true'
STOP = threading.Event()
ATTEMPTS = {}
LOCK = threading.Lock()
SCHEMA = [
    'CREATE TABLE IF NOT EXISTS drafts (owner VARCHAR(64) PRIMARY KEY, content TEXT NOT NULL, updated DOUBLE PRECISION NOT NULL)',
    '''CREATE TABLE IF NOT EXISTS jobs (id VARCHAR(36) PRIMARY KEY, owner VARCHAR(64) NOT NULL,
    content TEXT NOT NULL, voice VARCHAR(16) NOT NULL, provider VARCHAR(16) NOT NULL,
    status VARCHAR(16) NOT NULL, error TEXT NOT NULL, created DOUBLE PRECISION NOT NULL)''',
    'CREATE INDEX IF NOT EXISTS jobs_owner_created ON jobs(owner, created)',
]


def initialize():
    with DB.begin() as db:
        for statement in SCHEMA:
            db.execute(text(statement))
        # Additive migration preserves drafts, queued work, and existing audio.
        columns = {column['name'] for column in inspect(db).get_columns('jobs')}
        for column, default in (('rate', 'normal'), ('kind', 'reading')):
            if column not in columns:
                db.execute(text(f"ALTER TABLE jobs ADD COLUMN {column} VARCHAR(16) NOT NULL DEFAULT '{default}'"))
        # One application process only; recover interrupted tasks after restart.
        db.execute(text("UPDATE jobs SET status='queued' WHERE status='processing'"))


def cleanup():
    # Interrupted requests may leave private temporary uploads after a crash.
    for directory in DATA.glob('upload-*'):
        if directory.is_dir() and directory.stat().st_mtime < time.time() - 3600:
            shutil.rmtree(directory, ignore_errors=True)
    cutoff = time.time() - 86400
    with DB.begin() as db:
        ids = db.execute(text('SELECT id FROM jobs WHERE created < :c'), {'c': cutoff}).scalars().all()
        db.execute(text('DELETE FROM jobs WHERE created < :c'), {'c': cutoff})
        db.execute(text('DELETE FROM drafts WHERE updated < :c'), {'c': cutoff})
    for job_id in ids:
        for ext in ('mp3', 'wav', 'txt'):
            (DATA / f'{job_id}.{ext}').unlink(missing_ok=True)


def work():
    last_cleanup = 0
    while not STOP.wait(1):
        try:
            if time.time() - last_cleanup > 300:
                cleanup()
                last_cleanup = time.time()
            with DB.begin() as db:
                row = db.execute(text("SELECT * FROM jobs WHERE status='queued' ORDER BY created LIMIT 1")).mappings().first()
                if not row:
                    continue
                job = dict(row)
                db.execute(text("UPDATE jobs SET status='processing' WHERE id=:id"), job)
            source = DATA / (job['id'] + '.txt')
            target = DATA / (job['id'] + ('.mp3' if job['provider'] == 'edge' else '.wav'))
            try:
                source.write_text(job['content'], encoding='utf-8')
                subprocess.run([sys.executable, '-m', 'app.worker', 'tts', str(source), str(target),
                                job['voice'], job['provider'], job['rate']], timeout=200, check=True, capture_output=True)
                status, error = 'done', ''
            except Exception:
                target.unlink(missing_ok=True)
                status, error = 'failed', ('微软在线语音暂时不可用或生成超时，请稍后重试。未自动改用机械离线音色。' if job['provider'] == 'edge' else '离线语音生成失败或超时，请重试。')
            finally:
                source.unlink(missing_ok=True)
            with DB.begin() as db:
                db.execute(text('UPDATE jobs SET status=:s,error=:e WHERE id=:id'), {'s': status, 'e': error, 'id': job['id']})
        except Exception:
            # Do not print document text, database credentials, or subprocess output.
            import logging
            logging.getLogger(__name__).error('Worker operation failed; retrying')


@asynccontextmanager
async def lifespan(app):
    initialize()
    STOP.clear()
    thread = threading.Thread(target=work, daemon=True)
    thread.start()
    yield
    STOP.set()
    thread.join(timeout=2)


app = FastAPI(title='听见文件', lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)


@app.middleware('http')
async def security(request: Request, call_next):
    if request.method in {'POST', 'PUT', 'DELETE'}:
        if request.headers.get('origin') not in ORIGINS:
            from fastapi.responses import JSONResponse
            return JSONResponse({'detail': '请求来源不受信任，请从正式网页操作。'}, status_code=403)
    response = await call_next(request)
    response.headers.update({
        'X-Content-Type-Options': 'nosniff', 'Referrer-Policy': 'same-origin',
        'Content-Security-Policy': "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; media-src 'self' blob:; connect-src 'self'; frame-ancestors 'none'; form-action 'self'; base-uri 'none'",
        'Cache-Control': 'no-store',
    })
    return response


def owner(request):
    try:
        value = SIGNER.loads(request.cookies.get('reader_session', ''), max_age=86400)
        if not isinstance(value, str) or len(value) != 32:
            raise BadSignature('invalid')
        return value
    except (BadSignature, SignatureExpired):
        raise HTTPException(401, '请先输入访问口令。')


class Login(BaseModel):
    code: str = Field(max_length=128)


class Content(BaseModel):
    content: str = Field(max_length=30000)


class VoiceSettings(BaseModel):
    voice: Literal['female', 'xiaoyi', 'male'] = 'female'
    rate: Literal['slow', 'normal', 'fast'] = 'normal'


class Speech(Content, VoiceSettings):
    provider: Literal['offline', 'edge'] = 'offline'
    consent: bool = False


PREVIEW_TEXT = '你好，欢迎使用听见文件。让眼睛休息一下，我们慢慢来，把文字读给你听。'


@app.get('/api/health')
def health():
    with DB.connect() as db:
        db.execute(text('SELECT 1'))
    return {'status': 'ok'}


@app.post('/api/login')
def login(body: Login, request: Request):
    from fastapi.responses import JSONResponse
    ip = request.client.host if request.client else 'unknown'
    now = time.time()
    with LOCK:
        for key in list(ATTEMPTS):
            if now - ATTEMPTS[key][0] > 600:
                ATTEMPTS.pop(key)
        start, count = ATTEMPTS.get(ip, (now, 0))
        if count >= 10:
            raise HTTPException(429, '尝试过多，请 10 分钟后再试。')
        ATTEMPTS[ip] = (start, count + 1)
    if not hmac.compare_digest(hashlib.sha256(body.code.encode()).digest(), hashlib.sha256(ACCESS.encode()).digest()):
        raise HTTPException(401, '口令不正确。')
    response = JSONResponse({'ok': True})
    response.set_cookie('reader_session', SIGNER.dumps(secrets.token_hex(16)), httponly=True,
                        secure=COOKIE_SECURE, samesite='strict', max_age=86400)
    return response


@app.get('/api/session')
def session(request: Request):
    owner(request)
    from app.extract import SUPPORTED
    return {'edge_enabled': EDGE_ENABLED, 'extensions': sorted(SUPPORTED), 'native_mac': sys.platform == 'darwin'}


@app.post('/api/logout')
def logout(request: Request):
    from fastapi.responses import JSONResponse
    owner(request)
    response = JSONResponse({'ok': True})
    response.delete_cookie('reader_session')
    return response


@app.get('/api/draft')
def get_draft(request: Request):
    who = owner(request)
    with DB.connect() as db:
        content = db.execute(text('SELECT content FROM drafts WHERE owner=:o'), {'o': who}).scalar()
    return {'content': content or ''}


@app.put('/api/draft')
def save_draft(body: Content, request: Request):
    who = owner(request)
    with DB.begin() as db:
        db.execute(text('''INSERT INTO drafts(owner,content,updated) VALUES(:o,:c,:u)
        ON CONFLICT(owner) DO UPDATE SET content=:c,updated=:u'''), {'o': who, 'c': body.content, 'u': time.time()})
    return {'ok': True}


# Bound concurrent parsing across all users. The subprocess is killed after 60 seconds.
PARSERS = threading.BoundedSemaphore(2)


@app.post('/api/extract')
def upload(request: Request, file: UploadFile = File(...)):
    owner(request)
    from app.extract import SUPPORTED
    name = Path(file.filename or '').name[:160]
    if Path(name).suffix.lower() not in SUPPORTED:
        raise HTTPException(415, '暂不支持此类型，请查看页面上的支持格式。')
    if not PARSERS.acquire(blocking=False):
        raise HTTPException(429, '正在处理其他文件，请稍后重试。')
    try:
        with tempfile.TemporaryDirectory(dir=DATA, prefix='upload-') as temp:
            source, target = Path(temp) / 'upload', Path(temp) / 'text'
            size = 0
            with source.open('wb') as out:
                while chunk := file.file.read(65536):
                    size += len(chunk)
                    if size > 20 * 1024 * 1024:
                        raise HTTPException(413, '文件最多 20 MB，请拆分后上传。')
                    out.write(chunk)
            if not size:
                raise HTTPException(422, '文件为空。')
            try:
                result = subprocess.run([sys.executable, '-m', 'app.worker', 'extract', str(source), str(target), name],
                                        timeout=60, capture_output=True)
            except subprocess.TimeoutExpired:
                raise HTTPException(422, '文件处理超时，请拆分文件。')
            if result.returncode:
                error = Path(str(target) + '.error')
                raise HTTPException(422, error.read_text() if error.exists() else '文件损坏、格式不符或无法读取，请转换后重试。')
            return {'content': target.read_text(encoding='utf-8'), 'filename': name}
    finally:
        PARSERS.release()
        file.file.close()


@app.post('/api/speech', status_code=202)
def speech(body: Speech, request: Request):
    return enqueue(body, request)


@app.post('/api/preview', status_code=202)
def preview(body: VoiceSettings, request: Request):
    # Only this public sample is submitted; never read the user's draft.
    return enqueue(Speech(content=PREVIEW_TEXT, voice=body.voice, rate=body.rate,
                          provider='edge', consent=True), request, kind='preview')


def enqueue(body: Speech, request: Request, kind='reading'):
    who = owner(request)
    if not body.content.strip():
        raise HTTPException(422, '请先上传文件或输入文字。')
    if body.provider == 'edge' and (not EDGE_ENABLED or not body.consent):
        raise HTTPException(422, '在线语音未启用，或你尚未同意将文字发送至微软。')
    job = str(uuid.uuid4())
    with DB.begin() as db:
        # Serialize quota checks between sessions in PostgreSQL.
        if DB.dialect.name == 'postgresql':
            db.execute(text('SELECT pg_advisory_xact_lock(726341)'))
        pending = db.execute(text("SELECT COUNT(*) FROM jobs WHERE status IN ('queued','processing')")).scalar()
        count = db.execute(text('SELECT COUNT(*) FROM jobs WHERE owner=:o AND created>:t'), {'o': who, 't': time.time() - 3600}).scalar()
        if pending >= 20 or count >= 10:
            raise HTTPException(429, '队列已满或已达到每小时 10 次限制，请稍后再试。')
        db.execute(text('''INSERT INTO jobs(id,owner,content,voice,provider,rate,kind,status,error,created)
        VALUES(:id,:o,:c,:v,:p,:r,:k,'queued','',:t)'''),
                   {'id': job, 'o': who, 'c': body.content.strip(), 'v': body.voice, 'p': body.provider,
                    'r': body.rate, 'k': kind, 't': time.time()})
    return {'id': job, 'status': 'queued'}


@app.get('/api/jobs')
def jobs(request: Request):
    who = owner(request)
    with DB.connect() as db:
        rows = db.execute(text('SELECT id,status,error,provider,voice,rate,kind,created FROM jobs WHERE owner=:o ORDER BY created DESC LIMIT 20'), {'o': who}).mappings().all()
    return [dict(row) for row in rows]


@app.get('/api/audio/{job_id}')
def audio(job_id: uuid.UUID, request: Request):
    who = owner(request)
    with DB.connect() as db:
        job = db.execute(text("SELECT provider FROM jobs WHERE id=:id AND owner=:o AND status='done'"), {'id': str(job_id), 'o': who}).scalar()
    if job is None:
        raise HTTPException(404, '音频不存在或尚未生成。')
    ext = 'mp3' if job == 'edge' else 'wav'
    path = DATA / f'{job_id}.{ext}'
    if not path.exists():
        raise HTTPException(404, '音频已过期，请重新生成。')
    return FileResponse(path, media_type='audio/mpeg' if ext == 'mp3' else 'audio/wav', filename=f'朗读.{ext}')


@app.delete('/api/data')
def delete_data(request: Request):
    who = owner(request)
    with DB.begin() as db:
        if db.execute(text("SELECT COUNT(*) FROM jobs WHERE owner=:o AND status IN ('queued','processing')"), {'o': who}).scalar():
            raise HTTPException(409, '请等待任务结束后清除。')
        ids = db.execute(text('SELECT id FROM jobs WHERE owner=:o'), {'o': who}).scalars().all()
        db.execute(text('DELETE FROM jobs WHERE owner=:o'), {'o': who})
        db.execute(text('DELETE FROM drafts WHERE owner=:o'), {'o': who})
    for job_id in ids:
        for ext in ('mp3', 'wav', 'txt'):
            (DATA / f'{job_id}.{ext}').unlink(missing_ok=True)
    return {'ok': True}


app.mount('/', StaticFiles(directory=Path(__file__).parent / 'static', html=True), name='web')
