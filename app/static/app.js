'use strict';
const $ = id => document.getElementById(id);
const DRAFT_KEY = 'reader-draft-v1', NAME_KEY = 'reader-filename-v1';
let content = '', filename = '', busy = false, ready = false, onlineVoice = false, pollTimer, visibleCount = 20;
let ebookLimit = 80 * 1024 * 1024, standardLimit = 20 * 1024 * 1024, partLimit = 5000;
const isEbook = name => /\.(epub|mobi)$/i.test(name);
const isLongBook = () => isEbook(filename) && [...content].length > partLimit;
let chapterCount = 0;
let syncChain = Promise.resolve();
const message = text => { $('message').textContent = text; };
function readLocal(key) { try { return localStorage.getItem(key); } catch { return null; } }
function writeLocal(key, value) { try { localStorage.setItem(key, value); } catch {} }
function clearLocal() { try { localStorage.removeItem(DRAFT_KEY); localStorage.removeItem(NAME_KEY); } catch {} }
async function api(path, options = {}) {
  const response = await fetch('/api' + path, { credentials: 'same-origin', ...options });
  if (!response.ok) {
    let data; try { data = await response.json(); } catch {}
    throw new Error(typeof data?.detail === 'string' ? data.detail : '操作未成功，请稍后重试。');
  }
  return response.json();
}
const json = (method, body) => ({ method, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
function controls() {
  $('file').disabled = !ready || busy;
  $('generate').disabled = !ready || busy || !content.trim() || !onlineVoice;
  $('clear').disabled = busy;
}
function showFile(status) {
  $('selected').hidden = !content;
  $('filename').textContent = filename || '已保存的文件';
  $('file-state').textContent = status || `已提取 ${content.length.toLocaleString()} 字，可以生成朗读`;
  if (chapterCount) $('file-state').textContent += '。已识别 ' + chapterCount + ' 个章节标题，每章生成一条音频。';
  else if (isLongBook()) $('file-state').textContent += '。优先按章节生成，没有可靠目录时按阅读段生成。';
  controls();
}
function sync() {
  const saved = content, savedName = filename;
  syncChain = syncChain.then(async () => {
    try { await api('/draft', json('PUT', { content: saved, filename: savedName })); }
    catch { if (saved === content) $('file-state').textContent = '已保存在本机，联网后可继续'; }
  });
  return syncChain;
}
async function enter() {
  const session = await api('/session', json('POST', {}));
  onlineVoice = session.edge_enabled;
  ebookLimit = session.ebook_upload_bytes; standardLimit = session.standard_upload_bytes;
  partLimit = session.book_part_chars;
  $('file').accept = session.extensions.join(',');
  const draft = await api('/draft');
  content = readLocal(DRAFT_KEY) ?? draft.content;
  filename = readLocal(NAME_KEY) || draft.filename || '已保存的文件';
  ready = true; showFile();
  if (!onlineVoice) message('自然语音暂未启用，请稍后再来。');
  if (content !== draft.content) await sync();
  await refresh();
}
$('file').addEventListener('change', async () => {
  const file = $('file').files[0]; if (!file) return;
  const limit = isEbook(file.name) ? ebookLimit : standardLimit;
  if (file.size > limit) { message('该类型文件最多 ' + limit / (1024 ** 2) + ' MB，请拆分后上传。'); $('file').value = ''; return; }
  const data = new FormData(); data.append('file', file);
  busy = true; controls(); message('正在读取文件，请稍等…');
  try {
    const result = await api('/extract', { method: 'POST', body: data });
    content = result.content; filename = result.filename; chapterCount = result.chapter_count || 0;
    writeLocal(DRAFT_KEY, content); writeLocal(NAME_KEY, filename);
    showFile(); await sync(); message('文件已准备好，点击“生成朗读”即可。');
  } catch (e) { message(e.message); }
  finally { busy = false; $('file').value = ''; controls(); }
});
$('generate').addEventListener('click', async () => {
  if (busy || !content.trim() || !ready) return;
  busy = true; controls(); $('generate').textContent = '正在提交…';
  try {
    const result = await api(isEbook(filename) ? '/books' : '/speech', json('POST', { content, title: filename || '文件朗读', consent: true }));
    message(result.chaptered ? '已安排 ' + result.parts + ' 条章节音频，完成后可在历史中按章收听。' : result.parts > 1 ? '未识别到可靠章节，已按 ' + result.parts + ' 个阅读段安排朗读。' : '已开始生成，完成后在下方点击播放即可。'); await refresh();
    $('history-title').scrollIntoView({ behavior: 'smooth', block: 'start' });
  } catch (e) { message(e.message); }
  finally { busy = false; $('generate').textContent = '生成朗读'; controls(); }
});
function renderJob(job) {
  const node = document.createElement('article'); node.className = 'job'; node.dataset.id = job.id; node.dataset.status = job.status;
  const top = document.createElement('div'); top.className = 'job-top';
  const label = document.createElement('strong'); label.textContent = job.kind === 'preview' ? '声音试听' : job.title || '文件朗读';
  if (job.chapter_title) label.textContent = job.chapter_title;
  else if (job.book_id) label.textContent += ' · 第 ' + job.part_no + '/' + job.part_total + ' 部分';
  const state = document.createElement('span'); state.textContent = ({ scheduled: '等待前一部分', queued: '等待中', processing: '生成中…', done: '可以收听', failed: '生成失败' })[job.status];
  top.append(label, state); node.append(top);
  const meta = document.createElement('p'); meta.className = 'job-meta'; meta.textContent = new Date(job.created * 1000).toLocaleString('zh-CN', { hour12: false }) + (job.provider === 'edge' ? ' · 自然语音' : ' · 旧版离线录音'); if (job.chapter_title) meta.textContent = job.title + ' · ' + job.part_no + '/' + job.part_total + ' · ' + meta.textContent; node.append(meta);
  if (job.status === 'done') {
    const audio = document.createElement('audio'); audio.controls = true; audio.preload = 'none'; audio.src = '/api/audio/' + job.id;
    audio.addEventListener('error', () => message('音频加载失败，请检查网络；超过 30 天的录音需要重新生成。'));
    const link = document.createElement('a'); link.href = audio.src; link.download = ''; link.className = 'download'; link.textContent = '下载音频'; node.append(audio, link);
  }
  if (job.error) { const error = document.createElement('p'); error.className = 'error'; error.textContent = job.error; node.append(error); }
  const active = ['scheduled', 'queued', 'processing'].includes(job.status);
  const remove = document.createElement('button');
  remove.type = 'button'; remove.className = 'delete-job';
  remove.textContent = active ? '取消并删除' : '删除';
  remove.setAttribute('aria-label', remove.textContent + '：' + label.textContent);
  remove.addEventListener('click', async () => {
    if (!confirm(active ? '停止生成并删除这条朗读？' : '删除这条朗读及其音频？')) return;
    remove.disabled = true;
    try {
      await api('/jobs/' + job.id, { method: 'DELETE' });
      revision++;
      node.querySelector('audio')?.pause();
      node.remove();
      message(active ? '已取消并删除这条朗读。' : '朗读已删除。');
      await refresh();
    } catch (e) { remove.disabled = false; message(e.message); }
  });
  node.append(remove);
  if (job.book_id) {
    const removeBook = document.createElement('button');
    removeBook.type = 'button'; removeBook.className = 'delete-job';
    removeBook.textContent = '删除整本朗读';
    removeBook.addEventListener('click', async () => {
      if (!confirm('停止本书后续生成，并删除这次生成的所有部分？')) return;
      removeBook.disabled = true;
      try {
        await api('/books/' + job.book_id, { method: 'DELETE' });
        revision++; message('本次整本朗读已删除。'); await refresh();
      } catch (e) { removeBook.disabled = false; message(e.message); }
    });
    node.append(removeBook);
  }
  return node;
}
let refreshing = false, refreshAgain = false, revision = 0;
function poll() { refresh().catch(e => message(e.message)); }

async function refresh() {
  if (refreshing) { refreshAgain = true; return; }
  const version = revision;
  refreshing = true; clearTimeout(pollTimer);
  try {
    let jobs = [], lastPage = [];
    for (let offset = 0; offset < visibleCount; offset += 20) {
      lastPage = await api('/jobs?offset=' + offset); jobs.push(...lastPage);
      if (lastPage.length < 20) break;
    }
    if (version !== revision) return;
    const existing = new Map([...$('jobs').children].filter(x => x.dataset.id).map(x => [x.dataset.id, x]));
    $('jobs').querySelector('.empty')?.remove();
    for (const [index, job] of jobs.entries()) {
      let node = existing.get(job.id);
      if (!node || node.dataset.status !== job.status) {
        const replacement = renderJob(job);
        if (node) node.replaceWith(replacement);
        node = replacement;
      }
      // Preserve existing audio nodes; insert newly generated items at the top.
      if ($('jobs').children[index] !== node) $('jobs').insertBefore(node, $('jobs').children[index] || null);
      if (job.progress?.total > 1) node.querySelector('.job-top span').textContent = '生成中 · ' + job.progress.completed + '/' + job.progress.total + ' 段';
      existing.delete(job.id);
    }
    for (const node of existing.values()) node.remove();
    if (!jobs.length) { const p = document.createElement('p'); p.className = 'empty'; p.textContent = '生成的朗读会出现在这里。'; $('jobs').append(p); }
    $('more').hidden = lastPage.length < 20;
    if (jobs.some(x => ['queued', 'processing'].includes(x.status))) {
      const delay = jobs.some(x => x.status === 'processing') ? 800 : 1200;
      pollTimer = setTimeout(poll, delay);
    }
  } catch (e) {
    pollTimer = setTimeout(() => refresh().catch(e => message(e.message)), 10000);
    throw e;
  } finally {
    refreshing = false;
    if (refreshAgain) { refreshAgain = false; clearTimeout(pollTimer); pollTimer = setTimeout(poll, 0); }
  }
}
$('refresh').addEventListener('click', () => refresh().catch(e => message(e.message)));
$('more').addEventListener('click', () => { visibleCount += 20; refresh().catch(e => message(e.message)); });
$('clear').addEventListener('click', async () => {
  if (busy || !confirm('清除当前浏览器的草稿与全部朗读记录？此操作无法撤销。')) return;
  busy = true; controls();
  try { await syncChain; await api('/data', { method: 'DELETE' }); revision++; clearLocal(); content = ''; filename = ''; showFile(); visibleCount = 20; await refresh(); message('记录已清除。'); }
  catch (e) { message(e.message); } finally { busy = false; controls(); }
});
function network() { $('network').hidden = navigator.onLine; if (navigator.onLine && ready) sync(); }
window.addEventListener('online', network); window.addEventListener('offline', network); network();
enter().catch(e => message(e.message + ' 请刷新页面重试。'));
