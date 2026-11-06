'use strict';
const $ = id => document.getElementById(id);
let syncTimer, pollTimer, busy = false, restored = false, nativeMac = false;
const offlineNote = () => nativeMac ? '本机预览使用 macOS 已安装的婷婷中文音色。腾讯云 Linux 部署使用 eSpeak NG，音色会有所不同。' : '离线语音采用 eSpeak NG，音色较机械。文字不会发往外部语音服务；生成的音频通过服务器传给你。';
const DRAFT_KEY = 'reader-draft-v1';
let syncChain = Promise.resolve();
const message = text => { $('message').textContent = text; };
function localRead() { try { return localStorage.getItem(DRAFT_KEY); } catch { return null; } }
function localWrite(value) { try { localStorage.setItem(DRAFT_KEY, value); return true; } catch { return false; } }
function localClear() { try { localStorage.removeItem(DRAFT_KEY); } catch {} }
async function api(path, options = {}) {
  const response = await fetch('/api' + path, { credentials: 'same-origin', ...options });
  if (!response.ok) {
    let data; try { data = await response.json(); } catch {}
    if (response.status === 401) { $('workspace').hidden = true; $('login').hidden = false; clearTimeout(pollTimer); }
    throw new Error(typeof data?.detail === 'string' ? data.detail : '操作未成功，请检查内容或稍后重试。');
  }
  return response.json();
}
const json = (method, body) => ({ method, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
function updateCount() { $('count').textContent = `${$('content').value.length.toLocaleString()} / 30,000`; }
function sync() {
  if (!restored) return Promise.resolve();
  const value = $('content').value;
  syncChain = syncChain.then(async () => {
    try {
      await api('/draft', json('PUT', { content: value }));
      if (value === $('content').value) $('save-state').textContent = '已同步到服务器';
    } catch { $('save-state').textContent = '尚未同步，请保持页面打开'; }
  });
  return syncChain;
}
function changed() {
  updateCount();
  $('save-state').textContent = localWrite($('content').value) ? '已保存在本机' : '本机保存不可用，请保持页面打开';
  clearTimeout(syncTimer); syncTimer = setTimeout(sync, 900);
}
async function enter() {
  const session = await api('/session');
  $('login').hidden = true; $('workspace').hidden = false;
  nativeMac = session.native_mac; $('provider-note').textContent = offlineNote();
  $('edge-option').disabled = !session.edge_enabled;
  $('edge-option').textContent = session.edge_enabled ? '微软在线朗读 · 需要外网' : '微软在线朗读 · 尚未启用';
  $('file').accept = session.extensions.join(',');
  const server = await api('/draft');
  const local = localRead();
  $('content').value = local !== null ? local : server.content;
  restored = true; updateCount();
  if (local !== null && local !== server.content) await sync();
  await refresh();
}
$('login-form').addEventListener('submit', async event => {
  event.preventDefault(); const button = event.submitter; button.disabled = true;
  try { await api('/login', json('POST', { code: $('code').value })); $('code').value = ''; message(''); await enter(); }
  catch (e) { message(e.message); } finally { button.disabled = false; }
});
$('content').addEventListener('input', changed);
$('file').addEventListener('change', async () => {
  const file = $('file').files[0]; if (!file) return;
  if (file.size > 20 * 1024 * 1024) { message('文件最多 20 MB，请拆分后再上传。'); $('file').value = ''; return; }
  const data = new FormData(); data.append('file', file);
  $('file').disabled = true; $('generate').disabled = true; busy = true;
  message('正在上传并提取文字，图片识字可能需要约一分钟，请保持页面打开…');
  try {
    const result = await api('/extract', { method: 'POST', body: data });
    $('content').value = result.content; $('filename').textContent = '· ' + result.filename; changed();
    message('文字已提取。请校对内容，再生成朗读。');
  } catch (e) { message(e.message); }
  finally { $('file').disabled = false; $('generate').disabled = false; busy = false; $('file').value = ''; }
});
$('provider').addEventListener('change', () => {
  const online = $('provider').value === 'edge'; $('consent-row').hidden = !online; $('voice').disabled = !online;
  $('provider-note').textContent = online ? 'Edge-TTS 依赖外部微软服务，文字会发送至微软。中国大陆访问可能不稳定，服务可能中断；请勿上传保密资料。' : offlineNote();
  $('consent').checked = false;
});
$('generate').addEventListener('click', async () => {
  if (busy) return;
  if (!$('content').value.trim()) { message('请先上传文件或输入文字。'); $('content').focus(); return; }
  if ($('provider').value === 'edge' && !$('consent').checked) { message('使用微软在线语音前，请先确认文字发送授权。'); return; }
  busy = true; $('generate').disabled = true;
  try {
    await api('/speech', json('POST', { content: $('content').value, voice: $('voice').value, provider: $('provider').value, consent: $('consent').checked }));
    message('已加入朗读队列。生成完成后，在“我的朗读”中点击播放。'); await refresh();
  } catch (e) { message(e.message); } finally { busy = false; $('generate').disabled = false; }
});
async function refresh() {
  clearTimeout(pollTimer);
  const jobs = await api('/jobs');
  const existing = new Map([...$('jobs').children].filter(x => x.dataset.id).map(x => [x.dataset.id, x]));
  if (!jobs.length) { $('jobs').replaceChildren(); const p = document.createElement('p'); p.className = 'empty'; p.textContent = '还没有朗读。上传第一份文件，让文字变成声音。'; $('jobs').append(p); }
  else {
    $('jobs').querySelector('.empty')?.remove();
    for (const job of jobs) {
      let node = existing.get(job.id);
      if (node && node.dataset.status === job.status) { existing.delete(job.id); continue; }
      const replacement = document.createElement('article'); replacement.className = 'job'; replacement.dataset.id = job.id; replacement.dataset.status = job.status;
      const top = document.createElement('div'); top.className = 'job-top';
      const label = document.createElement('strong'); label.textContent = new Date(job.created * 1000).toLocaleString('zh-CN', { hour12: false }) + ' · ' + (job.provider === 'edge' ? '微软在线' : '离线中文');
      const status = document.createElement('span'); status.textContent = ({ queued: '等待中', processing: '生成中…', done: '可以收听', failed: '生成失败' })[job.status]; top.append(label, status); replacement.append(top);
      if (job.status === 'done') {
        const audio = document.createElement('audio'); audio.controls = true; audio.preload = 'none'; audio.src = '/api/audio/' + job.id;
        audio.addEventListener('error', () => message('音频加载失败，请检查网络或刷新列表，过期音频需要重新生成。'));
        const link = document.createElement('a'); link.href = audio.src; link.download = ''; link.className = 'download'; link.textContent = '下载音频 ↓'; replacement.append(audio, link);
      }
      if (job.error) { const error = document.createElement('p'); error.textContent = job.error; replacement.append(error); }
      if (node) { node.replaceWith(replacement); existing.delete(job.id); } else $('jobs').append(replacement);
    }
    for (const node of existing.values()) node.remove();
  }
  if (jobs.some(x => ['queued', 'processing'].includes(x.status))) pollTimer = setTimeout(() => refresh().catch(e => { message(e.message); pollTimer = setTimeout(() => refresh().catch(e => message(e.message)), 10000); }), 3000);
}
$('refresh').addEventListener('click', () => refresh().catch(e => message(e.message)));
$('clear').addEventListener('click', async () => {
  if (!confirm('清除本机草稿、服务器文字和音频？此操作无法撤销。')) return;
  try {
    clearTimeout(syncTimer); await syncChain; await api('/data', { method: 'DELETE' }); localClear(); $('content').value = ''; $('filename').textContent = ''; updateCount(); $('save-state').textContent = '内容已清除'; await refresh(); message('你的文字与音频已清除。');
  } catch (e) { message(e.message); }
});
$('logout').addEventListener('click', async () => {
  try { clearTimeout(syncTimer); await sync(); await api('/logout', { method: 'POST' }); localClear(); location.reload(); }
  catch (e) { message(e.message); }
});
function network() { $('network').hidden = navigator.onLine; if (navigator.onLine && restored) sync(); }
window.addEventListener('online', network); window.addEventListener('offline', network); network();
enter().catch(e => { if (!e.message.includes('请先输入')) message(e.message); });
