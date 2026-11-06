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

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import create_engine, inspect, text

DATA = Path(os.getenv('DATA_DIR', 'data')).resolve()
DATA.mkdir(parents=True, exist_ok=True)
SECRET = os.getenv('SESSION_SECRET', '')
if len(SECRET) < 32 or SECRET.startswith('REPLACE_'):
    raise RuntimeError('SESSION_SECRET 至少 32 字符；请配置 .env')
HISTORY_DAYS = 30
SESSION_AGE = 180 * 86400
MAX_AUDIO_BYTES = 5 * 1024 ** 3
MIN_FREE_BYTES = 1024 ** 3
SIGNER = URLSafeTimedSerializer(SECRET)
DB = create_engine(os.getenv('DATABASE_URL', 'sqlite:///' + str(DATA / 'reader.db')), pool_pre_ping=True)
ORIGINS = set(os.getenv('ALLOWED_ORIGINS', 'http://localhost:8000,http://127.0.0.1:8000').split(','))
COOKIE_SECURE = os.getenv('COOKIE_SECURE', 'true') == 'true'
EDGE_ENABLED = os.getenv('ENABLE_EDGE_TTS', 'true') == 'true'
STOP = threading.Event()
SCHEMA = [
    'CREATE TABLE IF NOT EXISTS drafts (owner VARCHAR(64) PRIMARY KEY, content TEXT NOT NULL, updated DOUBLE PRECISION NOT NULL)',
    '''CREATE TABLE IF NOT EXISTS jobs (id VARCHAR(36) PRIMARY KEY, owner VARCHAR(64) NOT NULL,
    content TEXT NOT NULL, voice VARCHAR(16) NOT NULL, provider VARCHAR(16) NOT NULL,
    status VARCHAR(16) NOT NULL, error TEXT NOT NULL, created DOUBLE PRECISION NOT NULL)''',
    'CREATE INDEX IF NOT EXISTS jobs_owner_created ON jobs(owner, created)',
    'CREATE TABLE IF NOT EXISTS usage_limits (bucket VARCHAR(48) PRIMARY KEY, count INTEGER NOT NULL, created DOUBLE PRECISION NOT NULL)',
]


def initialize():
    with DB.begin() as db:
        for statement in SCHEMA:
            db.execute(text(statement))
        # Additive migration preserves drafts, queued work, and existing audio.
        columns = {column['name'] for column in inspect(db).get_columns('jobs')}
        for column, default in (('rate', 'normal'), ('kind', 'reading'), ('title', '文件朗读')):
            if column not in columns:
                db.execute(text(f"ALTER TABLE jobs ADD COLUMN {column} VARCHAR(160) NOT NULL DEFAULT '{default}'"))
        # One application process only; recover interrupted tasks after restart.
        db.execute(text("UPDATE jobs SET status='queued' WHERE status='processing'"))


def cleanup():
    # Interrupted requests may leave private temporary uploads after a crash.
    for directory in DATA.glob('upload-*'):
        if directory.is_dir() and directory.stat().st_mtime < time.time() - 3600:
            shutil.rmtree(directory, ignore_errors=True)
    cutoff = time.time() - HISTORY_DAYS * 86400
    with DB.begin() as db:
        ids = db.execute(text('SELECT id FROM jobs WHERE created < :c'), {'c': cutoff}).scalars().all()
        db.execute(text('DELETE FROM jobs WHERE created < :c'), {'c': cutoff})
        db.execute(text('DELETE FROM drafts WHERE updated < :c'), {'c': time.time() - 86400})
        db.execute(text('DELETE FROM usage_limits WHERE created < :c'), {'c': time.time() - 172800})
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
        value = SIGNER.loads(request.cookies.get('reader_session', ''), max_age=SESSION_AGE)
        if not isinstance(value, str) or len(value) != 32:
            raise BadSignature('invalid')
        return value
    except (BadSignature, SignatureExpired):
        raise HTTPException(401, '浏览器记录已失效，请刷新页面后重试。')


class Content(BaseModel):
    content: str = Field(max_length=30000)


class Speech(Content):
    model_config = ConfigDict(extra='forbid')
    title: str = Field(default='文件朗读', max_length=160)
    consent: bool = False


def reserve_usage(db, name, period, limit):
    now = time.time()
    bucket = f'{name}:{int(now // period)}'
    count = db.execute(text("""INSERT INTO usage_limits(bucket,count,created) VALUES(:b,1,:t)
        ON CONFLICT(bucket) DO UPDATE SET count=usage_limits.count+1 RETURNING count"""),
                       {'b': bucket, 't': now}).scalar_one()
    if count > limit:
        raise HTTPException(429, '当前使用人数较多，请稍后再试。')


def check_storage():
    used = sum(p.stat().st_size for p in DATA.iterdir() if p.is_file() and p.suffix in {'.mp3', '.wav'})
    if used >= MAX_AUDIO_BYTES or shutil.disk_usage(DATA).free < MIN_FREE_BYTES:
        raise HTTPException(503, '存储空间暂时不足，请稍后再试。')


@app.get('/api/health')
def health():
    with DB.connect() as db:
        db.execute(text('SELECT 1'))
    return {'status': 'ok'}


@app.post('/api/session')
def session(request: Request):
    # A private browser identity, not a shared public history. Preserve old cookies.
    from fastapi.responses import JSONResponse
    from app.extract import SUPPORTED
    try:
        who = owner(request)
    except HTTPException:
        who = secrets.token_hex(16)
    response = JSONResponse({'edge_enabled': EDGE_ENABLED, 'extensions': sorted(SUPPORTED),
                             'history_days': HISTORY_DAYS})
    response.set_cookie('reader_session', SIGNER.dumps(who), httponly=True,
                        secure=COOKIE_SECURE, samesite='strict', max_age=SESSION_AGE)
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
        reserve_usage(db, 'drafts-hour', 3600, 600)
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
    check_storage()
    with DB.begin() as db:
        reserve_usage(db, 'uploads-hour', 3600, 120)
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


def enqueue(body: Speech, request: Request):
    who = owner(request)
    if not body.content.strip():
        raise HTTPException(422, '请先上传文件或输入文字。')
    if not EDGE_ENABLED or not body.consent:
        raise HTTPException(422, '在线语音未启用，或你尚未同意将文字发送至微软。')
    check_storage()
    job = str(uuid.uuid4())
    with DB.begin() as db:
        # Serialize quota checks between sessions in PostgreSQL.
        if DB.dialect.name == 'postgresql':
            db.execute(text('SELECT pg_advisory_xact_lock(726341)'))
        pending = db.execute(text("SELECT COUNT(*) FROM jobs WHERE status IN ('queued','processing')")).scalar()
        count = db.execute(text('SELECT COUNT(*) FROM jobs WHERE owner=:o AND created>:t'), {'o': who, 't': time.time() - 3600}).scalar()
        if pending >= 20 or count >= 10:
            raise HTTPException(429, '队列已满或已达到每小时 10 次限制，请稍后再试。')
        reserve_usage(db, 'speech-hour', 3600, 100)
        reserve_usage(db, 'speech-day', 86400, 500)
        db.execute(text('''INSERT INTO jobs(id,owner,content,voice,provider,rate,kind,title,status,error,created)
        VALUES(:id,:o,:c,'female','edge','slow','reading',:title,'queued','',:t)'''),
                   {'id': job, 'o': who, 'c': body.content.strip(),
                    'title': body.title.strip() or '文件朗读', 't': time.time()})
    return {'id': job, 'status': 'queued'}


@app.get('/api/jobs')
def jobs(request: Request, offset: int = 0):
    who = owner(request)
    if offset < 0 or offset > 10000:
        raise HTTPException(422, '历史页码无效。')
    with DB.connect() as db:
        rows = db.execute(text('SELECT id,status,error,provider,voice,rate,kind,title,created FROM jobs WHERE owner=:o ORDER BY created DESC,id DESC LIMIT 20 OFFSET :offset'), {'o': who, 'offset': offset}).mappings().all()
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
