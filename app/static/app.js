'use strict';
const $ = id => document.getElementById(id);
const DRAFT_KEY = 'reader-draft-v1', NAME_KEY = 'reader-filename-v1';
let content = '', filename = '', busy = false, ready = false, onlineVoice = false, pollTimer, visibleCount = 20;
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
  controls();
}
function sync() {
  const saved = content;
  syncChain = syncChain.then(async () => {
    try { await api('/draft', json('PUT', { content: saved })); }
    catch { if (saved === content) $('file-state').textContent = '已保存在本机，联网后可继续'; }
  });
  return syncChain;
}
async function enter() {
  const session = await api('/session', json('POST', {}));
  onlineVoice = session.edge_enabled;
  $('file').accept = session.extensions.join(',');
  const draft = await api('/draft');
  content = readLocal(DRAFT_KEY) ?? draft.content;
  filename = readLocal(NAME_KEY) || '已保存的文件';
  ready = true; showFile();
  if (!onlineVoice) message('自然语音暂未启用，请稍后再来。');
  if (content !== draft.content) await sync();
  await refresh();
}
$('file').addEventListener('change', async () => {
  const file = $('file').files[0]; if (!file) return;
  if (file.size > 20 * 1024 * 1024) { message('文件最多 20 MB，请拆分后上传。'); $('file').value = ''; return; }
  const data = new FormData(); data.append('file', file);
  busy = true; controls(); message('正在读取文件，请稍等…');
  try {
    const result = await api('/extract', { method: 'POST', body: data });
    content = result.content; filename = result.filename;
    writeLocal(DRAFT_KEY, content); writeLocal(NAME_KEY, filename);
    showFile(); await sync(); message('文件已准备好，点击“生成朗读”即可。');
  } catch (e) { message(e.message); }
  finally { busy = false; $('file').value = ''; controls(); }
});
$('generate').addEventListener('click', async () => {
  if (busy || !content.trim() || !ready) return;
  busy = true; controls(); $('generate').textContent = '正在提交…';
  try {
    await api('/speech', json('POST', { content, title: filename || '文件朗读', consent: true }));
    message('已开始生成，完成后在下方点击播放即可。'); await refresh();
    $('history-title').scrollIntoView({ behavior: 'smooth', block: 'start' });
  } catch (e) { message(e.message); }
  finally { busy = false; $('generate').textContent = '生成朗读'; controls(); }
});
function renderJob(job) {
  const node = document.createElement('article'); node.className = 'job'; node.dataset.id = job.id; node.dataset.status = job.status;
  const top = document.createElement('div'); top.className = 'job-top';
  const label = document.createElement('strong'); label.textContent = job.kind === 'preview' ? '声音试听' : job.title || '文件朗读';
  const state = document.createElement('span'); state.textContent = ({ queued: '等待中', processing: '生成中…', done: '可以收听', failed: '生成失败' })[job.status];
  top.append(label, state); node.append(top);
  const meta = document.createElement('p'); meta.className = 'job-meta'; meta.textContent = new Date(job.created * 1000).toLocaleString('zh-CN', { hour12: false }) + (job.provider === 'edge' ? ' · 自然语音' : ' · 旧版离线录音'); node.append(meta);
  if (job.status === 'done') {
    const audio = document.createElement('audio'); audio.controls = true; audio.preload = 'none'; audio.src = '/api/audio/' + job.id;
    audio.addEventListener('error', () => message('音频加载失败，请检查网络；超过 30 天的录音需要重新生成。'));
    const link = document.createElement('a'); link.href = audio.src; link.download = ''; link.className = 'download'; link.textContent = '下载音频'; node.append(audio, link);
  }
  if (job.error) { const error = document.createElement('p'); error.className = 'error'; error.textContent = job.error; node.append(error); }
  return node;
}
let refreshing = false;
async function refresh() {
  if (refreshing) return;
  refreshing = true; clearTimeout(pollTimer);
  try {
    let jobs = [], lastPage = [];
    for (let offset = 0; offset < visibleCount; offset += 20) {
      lastPage = await api('/jobs?offset=' + offset); jobs.push(...lastPage);
      if (lastPage.length < 20) break;
    }
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
      existing.delete(job.id);
    }
    for (const node of existing.values()) node.remove();
    if (!jobs.length) { const p = document.createElement('p'); p.className = 'empty'; p.textContent = '生成的朗读会出现在这里。'; $('jobs').append(p); }
    $('more').hidden = lastPage.length < 20;
    if (jobs.some(x => ['queued', 'processing'].includes(x.status))) pollTimer = setTimeout(() => refresh().catch(e => message(e.message)), 3000);
  } catch (e) {
    pollTimer = setTimeout(() => refresh().catch(e => message(e.message)), 10000);
    throw e;
  } finally { refreshing = false; }
}
$('refresh').addEventListener('click', () => refresh().catch(e => message(e.message)));
$('more').addEventListener('click', () => { visibleCount += 20; refresh().catch(e => message(e.message)); });
$('clear').addEventListener('click', async () => {
  if (busy || !confirm('清除当前浏览器的草稿与全部朗读记录？此操作无法撤销。')) return;
  busy = true; controls();
  try { await syncChain; await api('/data', { method: 'DELETE' }); clearLocal(); content = ''; filename = ''; showFile(); visibleCount = 20; await refresh(); message('记录已清除。'); }
  catch (e) { message(e.message); } finally { busy = false; controls(); }
});
function network() { $('network').hidden = navigator.onLine; if (navigator.onLine && ready) sync(); }
window.addEventListener('online', network); window.addEventListener('offline', network); network();
enter().catch(e => message(e.message + ' 请刷新页面重试。'));
