import os
import signal
import logging
import json
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

from app.reading_text import spoken_text

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import create_engine, inspect, text

DATA = Path(os.getenv('DATA_DIR', 'data')).resolve()
DATA.mkdir(parents=True, exist_ok=True)
(DATA / 'spool').mkdir(mode=0o700, exist_ok=True)
SECRET = os.getenv('SESSION_SECRET', '')
if len(SECRET) < 32 or SECRET.startswith('REPLACE_'):
    raise RuntimeError('SESSION_SECRET 至少 32 字符；请配置 .env')
EBOOK_EXTENSIONS = {'.mobi', '.epub'}
EBOOK_UPLOAD = 80 * 1024 * 1024
STANDARD_UPLOAD = 20 * 1024 * 1024
BOOK_PART = 5000
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
QUEUE = threading.Condition()
RUNNING = {}
WORKER_COUNT = 2
SCHEMA = [
    'CREATE TABLE IF NOT EXISTS drafts (owner VARCHAR(64) PRIMARY KEY, content TEXT NOT NULL, updated DOUBLE PRECISION NOT NULL)',
    '''CREATE TABLE IF NOT EXISTS jobs (id VARCHAR(36) PRIMARY KEY, owner VARCHAR(64) NOT NULL,
    content TEXT NOT NULL, voice VARCHAR(16) NOT NULL, provider VARCHAR(16) NOT NULL,
    status VARCHAR(16) NOT NULL, error TEXT NOT NULL, created DOUBLE PRECISION NOT NULL)''',
    'CREATE TABLE IF NOT EXISTS speech_usage (job_id VARCHAR(36) PRIMARY KEY, owner VARCHAR(64) NOT NULL, created DOUBLE PRECISION NOT NULL)',
    'CREATE INDEX IF NOT EXISTS usage_owner_time ON speech_usage(owner,created)',
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
        for column, definition in (('book_id', "VARCHAR(36) NOT NULL DEFAULT ''"),
                                   ('part_no', 'INTEGER NOT NULL DEFAULT 1'),
                                   ('part_total', 'INTEGER NOT NULL DEFAULT 1'),
                                   ('ready_at', 'DOUBLE PRECISION NOT NULL DEFAULT 0')):
            if column not in columns:
                db.execute(text(f'ALTER TABLE jobs ADD COLUMN {column} {definition}'))
        if 'filename' not in {col['name'] for col in inspect(db).get_columns('drafts')}:
            db.execute(text("ALTER TABLE drafts ADD COLUMN filename VARCHAR(160) NOT NULL DEFAULT ''"))
        db.execute(text('CREATE INDEX IF NOT EXISTS jobs_book_parts ON jobs(book_id,status,part_no)'))
        db.execute(text('''INSERT INTO speech_usage(job_id,owner,created)
            SELECT id,owner,created FROM jobs WHERE created>:cutoff AND part_no=1
            ON CONFLICT(job_id) DO NOTHING'''), {'cutoff': time.time() - 3600})
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
        db.execute(text('DELETE FROM speech_usage WHERE created < :c'), {'c': time.time() - 3600})
        db.execute(text('DELETE FROM usage_limits WHERE created < :c'), {'c': time.time() - 172800})
    for job_id in ids:
        for ext in ('mp3', 'wav', 'txt'):
            (DATA / f'{job_id}.{ext}').unlink(missing_ok=True)


def workdir(job_id):
    return DATA / ('job-' + job_id)


def remove_files(job_id):
    for ext in ('mp3', 'wav', 'txt'):
        (DATA / f'{job_id}.{ext}').unlink(missing_ok=True)
    directory = workdir(job_id)
    if directory.exists():
        shutil.rmtree(directory)


def stop_process(proc):
    # The synthesizer and its audio tools share an isolated process group.
    if proc.poll() is None:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        proc.wait(timeout=5)


def launch(job):
    directory = workdir(job['id'])
    directory.mkdir(mode=0o700)
    source = directory / 'source.txt'
    source.write_text(job['content'], encoding='utf-8')
    ext = 'mp3' if job['provider'] == 'edge' else 'wav'
    target = directory / ('audio.' + ext)
    proc = subprocess.Popen([sys.executable, '-m', 'app.worker', 'tts', str(source), str(target),
                             job['voice'], job['provider'], job['rate']], start_new_session=True,
                            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return proc, target


def advance_book(db, book_id):
    if book_id:
        db.execute(text("""UPDATE jobs SET status='queued',ready_at=:now WHERE id=(
            SELECT id FROM jobs WHERE book_id=:b AND status='scheduled' ORDER BY part_no LIMIT 1)
            AND NOT EXISTS (SELECT 1 FROM jobs WHERE book_id=:b AND status IN ('processing','queued'))"""),
                   {'b': book_id, 'now': time.time()})


def run_next():
    with QUEUE:
        if STOP.is_set():
            return
        with DB.begin() as db:
            row = db.execute(text("SELECT * FROM jobs WHERE status='queued' ORDER BY CASE WHEN ready_at=0 THEN created ELSE ready_at END,id LIMIT 1")).mappings().first()
            if row:
                job = dict(row)
                db.execute(text("UPDATE jobs SET status='processing' WHERE id=:id"), job)
        if not row:
            QUEUE.wait(timeout=1)
            return
        job_id = job['id']
        try:
            proc, target = launch(job)
        except Exception:
            remove_files(job_id)
            with DB.begin() as db:
                db.execute(text("UPDATE jobs SET status='failed',error=:e WHERE id=:id"),
                           {'id': job_id, 'e': '无法启动朗读，请重新生成。'})
                advance_book(db, job['book_id'])
            return
        RUNNING[job_id] = proc

    ok = False
    try:
        ok = proc.wait(timeout=200) == 0
    except subprocess.TimeoutExpired:
        with QUEUE:
            stop_process(proc)
        ok = False
    finally:
        with QUEUE:
            RUNNING.pop(job_id, None)
            try:
                with DB.begin() as db:
                    exists = db.execute(text("SELECT 1 FROM jobs WHERE id=:id AND status='processing'"),
                                        {'id': job_id}).scalar()
                    if exists:
                        if STOP.is_set():
                            status, error = 'queued', ''
                        elif ok and target.is_file() and target.stat().st_size:
                            target.replace(DATA / (job_id + target.suffix))
                            status, error = 'done', ''
                        else:
                            status, error = 'failed', '朗读生成失败或超时，请稍后重试。'
                        db.execute(text('UPDATE jobs SET status=:s,error=:e WHERE id=:id'),
                                   {'id': job_id, 's': status, 'e': error})
                        advance_book(db, job['book_id'])
            finally:
                shutil.rmtree(workdir(job_id), ignore_errors=True)
                QUEUE.notify_all()


def work(maintain=True):
    last_cleanup = 0
    while not STOP.is_set():
        try:
            if maintain and time.monotonic() - last_cleanup > 300:
                with QUEUE:
                    cleanup()
                last_cleanup = time.monotonic()
            run_next()
        except Exception:
            logging.getLogger(__name__).error('Worker operation failed; retrying')
            STOP.wait(1)


@asynccontextmanager
async def lifespan(app):
    initialize()
    # A terminated container may leave private, unpublished segment files behind.
    for directory in DATA.glob('job-*'):
        if directory.is_dir():
            shutil.rmtree(directory)
    with DB.connect() as db:
        known = set(db.execute(text('SELECT id FROM jobs')).scalars())
    for path in DATA.iterdir():
        if path.suffix in {'.mp3', '.wav', '.txt'} and path.stem not in known:
            try:
                uuid.UUID(path.stem)
            except ValueError:
                continue
            path.unlink(missing_ok=True)
    STOP.clear()
    threads = [threading.Thread(target=work, args=(i == 0,), daemon=True) for i in range(WORKER_COUNT)]
    for thread in threads:
        thread.start()
    try:
        yield
    finally:
        with QUEUE:
            STOP.set()
            for proc in list(RUNNING.values()):
                stop_process(proc)
            QUEUE.notify_all()
        for thread in threads:
            thread.join(timeout=5)


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


class Draft(BaseModel):
    content: str = Field(max_length=300000)
    filename: str = Field(default='', max_length=160)


class Speech(Content):
    model_config = ConfigDict(extra='forbid')
    title: str = Field(default='文件朗读', max_length=160)
    consent: bool = False


class BookSpeech(Speech):
    content: str = Field(max_length=300000)


def reserve_usage(db, name, period, limit, amount=1):
    now = time.time()
    bucket = f'{name}:{int(now // period)}'
    count = db.execute(text("""INSERT INTO usage_limits(bucket,count,created) VALUES(:b,:amount,:t)
        ON CONFLICT(bucket) DO UPDATE SET count=usage_limits.count+:amount RETURNING count"""),
                       {'b': bucket, 't': now, 'amount': amount}).scalar_one()
    if count > limit:
        raise HTTPException(429, '当前使用人数较多，请稍后再试。')


def check_storage():
    used = 0
    for path in DATA.iterdir():
        if path.suffix in {'.mp3', '.wav'}:
            try:
                used += path.stat().st_size
            except FileNotFoundError:
                continue  # A user deleted this audio during the scan.
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
                             'history_days': HISTORY_DAYS, 'ebook_upload_bytes': EBOOK_UPLOAD,
                             'standard_upload_bytes': STANDARD_UPLOAD, 'book_part_chars': BOOK_PART})
    response.set_cookie('reader_session', SIGNER.dumps(who), httponly=True,
                        secure=COOKIE_SECURE, samesite='strict', max_age=SESSION_AGE)
    return response


@app.get('/api/draft')
def get_draft(request: Request):
    who = owner(request)
    with DB.connect() as db:
        row = db.execute(text('SELECT content,filename FROM drafts WHERE owner=:o'), {'o': who}).mappings().first()
    return dict(row) if row else {'content': '', 'filename': ''}


@app.put('/api/draft')
def save_draft(body: Draft, request: Request):
    who = owner(request)
    if len(body.content) > 30000 and Path(body.filename).suffix.lower() not in EBOOK_EXTENSIONS:
        raise HTTPException(422, '普通文件最多 3 万字。')
    with DB.begin() as db:
        reserve_usage(db, 'drafts-hour', 3600, 600)
        db.execute(text('''INSERT INTO drafts(owner,content,updated,filename) VALUES(:o,:c,:u,:f)
        ON CONFLICT(owner) DO UPDATE SET content=:c,updated=:u,filename=:f'''), {'o': who, 'c': body.content, 'u': time.time(), 'f': body.filename})
    return {'ok': True}


# Bound concurrent parsing across all users. The subprocess is killed after 60 seconds.
PARSERS = threading.BoundedSemaphore(2)
BOOK_PARSER = threading.BoundedSemaphore(1)


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
    is_book = Path(name).suffix.lower() in EBOOK_EXTENSIONS
    maximum = EBOOK_UPLOAD if is_book else STANDARD_UPLOAD
    if not PARSERS.acquire(blocking=False):
        raise HTTPException(429, '正在处理其他文件，请稍后重试。')
    if is_book and not BOOK_PARSER.acquire(blocking=False):
        PARSERS.release()
        raise HTTPException(429, '正在处理另一本电子书，请稍后再上传。')
    try:
        with tempfile.TemporaryDirectory(dir=DATA, prefix='upload-') as temp:
            source, target = Path(temp) / 'upload', Path(temp) / 'text'
            size = 0
            with source.open('wb') as out:
                while chunk := file.file.read(65536):
                    size += len(chunk)
                    if size > maximum:
                        raise HTTPException(413, f'此类型文件最多 {maximum // (1024 ** 2)} MB，请拆分后上传。')
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
        if is_book:
            BOOK_PARSER.release()
        PARSERS.release()
        file.file.close()


@app.post('/api/speech', status_code=202)
def speech(body: Speech, request: Request):
    return enqueue(body, request)


def book_parts(content):
    parts = []
    while len(content) > BOOK_PART:
        limit = 100 if not parts else BOOK_PART
        window = content[:limit]
        cut = max((window.rfind(mark) + 1 for mark in '。！？；\n'), default=0)
        if cut < limit * 4 // 5:
            cut = limit
        parts.append(content[:cut])
        content = content[cut:]
    parts.append(content)
    return [part for part in parts if part.strip()]


@app.post('/api/books', status_code=202)
def submit_book(body: BookSpeech, request: Request):
    if Path(body.title).suffix.lower() not in EBOOK_EXTENSIONS:
        raise HTTPException(422, '整本生成适用于 EPUB 和 MOBI。')
    return enqueue(body, request, whole_book=True)


def enqueue(body: Speech, request: Request, whole_book=False):
    who = owner(request)
    content = body.content.strip()
    if not content:
        raise HTTPException(422, '请先上传文件或输入文字。')
    content = spoken_text(content, body.title)
    if not content:
        raise HTTPException(422, '跳过出版、通讯和页码信息后，没有可朗读的正文。')
    parts = book_parts(content) if whole_book else [content]
    book_id = str(uuid.uuid4()) if len(parts) > 1 else ''
    if not EDGE_ENABLED or not body.consent:
        raise HTTPException(422, '在线语音未启用，或你尚未同意将文字发送至微软。')
    check_storage()
    job = str(uuid.uuid4())
    with QUEUE, DB.begin() as db:
        # Serialize quota checks between sessions in PostgreSQL.
        if DB.dialect.name == 'postgresql':
            db.execute(text('SELECT pg_advisory_xact_lock(726341)'))
        pending = db.execute(text("SELECT COUNT(*) FROM jobs WHERE status IN ('queued','processing')")).scalar()
        count = db.execute(text('SELECT COUNT(*) FROM speech_usage WHERE owner=:o AND created>:t'), {'o': who, 't': time.time() - 3600}).scalar()
        if pending >= 20 or count >= 10:
            raise HTTPException(429, '队列已满或已达到每小时 10 次限制，请稍后再试。')
        if book_id:
            busy = db.execute(text("""SELECT owner FROM jobs WHERE book_id<>'' AND
                status IN ('queued','processing','scheduled') GROUP BY owner""")).scalars().all()
            if who in busy or len(busy) >= 3:
                raise HTTPException(429, '已有整本书在生成，请稍后再试。')
        reserve_usage(db, 'speech-hour', 3600, 100, len(parts))
        reserve_usage(db, 'speech-day', 86400, 500, len(parts))
        created = time.time()
        values = [{'id': job if i == 1 else str(uuid.uuid4()), 'owner': who, 'part': part,
                   'book': book_id, 'number': i, 'total': len(parts), 'title': body.title.strip() or '文件朗读',
                   'status': 'queued' if i == 1 else 'scheduled', 'created': created}
                  for i, part in enumerate(parts, 1)]
        db.execute(text("""INSERT INTO jobs(id,owner,content,voice,provider,rate,kind,title,status,error,created,book_id,part_no,part_total)
            VALUES(:id,:owner,:part,'female','edge','slow','reading',:title,:status,'',:created,:book,:number,:total)"""), values)
        db.execute(text('INSERT INTO speech_usage(job_id,owner,created) VALUES(:id,:o,:t)'),
                   {'id': job, 'o': who, 't': time.time()})
        # Waiters reacquire QUEUE only after the transaction commits.
        QUEUE.notify_all()
    return {'id': job, 'status': 'queued', 'book_id': book_id, 'parts': len(parts)}


@app.get('/api/jobs')
def jobs(request: Request, offset: int = 0):
    who = owner(request)
    if offset < 0 or offset > 10000:
        raise HTTPException(422, '历史页码无效。')
    with DB.connect() as db:
        rows = db.execute(text('SELECT id,status,error,provider,voice,rate,kind,title,created,book_id,part_no,part_total FROM jobs WHERE owner=:o ORDER BY created DESC,part_no,id DESC LIMIT 20 OFFSET :offset'), {'o': who, 'offset': offset}).mappings().all()
    items = [dict(row) for row in rows]
    for item in items:
        if item['status'] == 'processing':
            try:
                item['progress'] = json.loads((workdir(item['id']) / 'progress.json').read_text())
            except (FileNotFoundError, ValueError):
                pass
    return items


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


def delete_readings(who, job_id=None, book_id=None):
    with QUEUE:
        # Commit removal before stopping a process. Finalization also holds QUEUE
        # and cannot publish output for a row that no longer exists.
        with DB.begin() as db:
            params = {'o': who}
            where = 'owner=:o'
            if job_id is not None:
                where += ' AND id=:id'
                params['id'] = job_id
            if book_id is not None:
                where += ' AND book_id=:b'
                params['b'] = book_id
            rows = db.execute(text('SELECT id,book_id FROM jobs WHERE ' + where), params).all()
            ids = [row.id for row in rows]
            if (job_id is not None or book_id is not None) and not ids:
                raise HTTPException(404, '此朗读不存在或已删除。')
            db.execute(text('DELETE FROM jobs WHERE ' + where), params)
            if job_id is not None:
                advance_book(db, rows[0].book_id)
            if job_id is None and book_id is None:
                db.execute(text('DELETE FROM drafts WHERE owner=:o'), params)
        for removed in ids:
            proc = RUNNING.get(removed)
            if proc is not None:
                stop_process(proc)
            remove_files(removed)
        QUEUE.notify_all()
    return {'ok': True}


@app.delete('/api/jobs/{job_id}')
def delete_job(job_id: uuid.UUID, request: Request):
    return delete_readings(owner(request), str(job_id))


@app.delete('/api/books/{book_id}')
def delete_book(book_id: uuid.UUID, request: Request):
    return delete_readings(owner(request), book_id=str(book_id))


@app.delete('/api/data')
def delete_data(request: Request):
    return delete_readings(owner(request))


app.mount('/', StaticFiles(directory=Path(__file__).parent / 'static', html=True), name='web')
