const MAX_LOG_LINES = 2000;
let pollTimer = null;

function $(id) { return document.getElementById(id); }

function setBadge(running) {
  const badge = $('status-badge');
  badge.textContent = running ? '运行中' : '未运行';
  badge.className = 'badge ' + (running ? 'running' : 'stopped');
  $('btn-start').disabled = running;
  $('btn-stop').disabled = !running;
}

async function refreshStatus() {
  try {
    const r = await fetch('/api/status');
    const s = await r.json();
    setBadge(s.running);
    $('status-pid').textContent = s.running ? s.pid : '—';
  } catch (e) { /* ignore */ }
}

function appendLog(lines) {
  if (!lines || !lines.length) return;
  const panel = $('log-panel');
  let text = panel.textContent;
  if (text) text += '\n';
  text += lines.join('\n');
  const all = text.split('\n');
  if (all.length > MAX_LOG_LINES) {
    text = all.slice(all.length - MAX_LOG_LINES).join('\n');
  }
  panel.textContent = text;
  panel.scrollTop = panel.scrollHeight;
}

async function initLogs() {
  try {
    const r = await fetch('/api/logs?tail=300');
    const d = await r.json();
    $('log-panel').textContent = d.lines.join('\n');
    $('log-panel').scrollTop = $('log-panel').scrollHeight;
  } catch (e) { /* ignore */ }
  startPolling();
}

async function pollLogs() {
  try {
    const r = await fetch('/api/logs');
    const d = await r.json();
    appendLog(d.lines);
  } catch (e) { /* ignore */ }
}

function startPolling() {
  if (pollTimer) clearInterval(pollTimer);
  pollTimer = setInterval(pollLogs, 1500);
}

async function start() {
  const r = await fetch('/api/start', { method: 'POST' });
  const d = await r.json();
  toast(d.msg);
  await refreshStatus();
  await initLogs();
}

async function stop() {
  const r = await fetch('/api/stop', { method: 'POST' });
  const d = await r.json();
  toast(d.msg);
  await refreshStatus();
  try {
    const fr = await fetch('/api/logs?flush=1');
    const fd = await fr.json();
    appendLog(fd.lines);
  } catch (e) { /* ignore */ }
}

async function loadConfig() {
  const r = await fetch('/api/config');
  const d = await r.json();
  $('config-editor').value = d.config_text || '';
  const acc = d.account || {};
  $('acc-username').value = acc.steam_username || '';
  $('acc-password').value = acc.steam_password || '';
  $('acc-shared').value = acc.shared_secret || '';
  $('acc-identity').value = acc.identity_secret || '';
  $('account-editor').value = d.account_text || '';
}

async function saveConfig() {
  const content = $('config-editor').value;
  const r = await fetch('/api/config/save', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ content }),
  });
  const d = await r.json();
  showMsg('config-msg', d.msg, d.ok);
}

async function saveAccount() {
  // 文本视图激活时，先尝试从文本同步表单（校验格式）
  if ($('acc-text-view').style.display !== 'none') {
    if (!accTextToForm()) {
      showMsg('account-msg', '文本 JSON 格式错误，无法保存', false);
      return;
    }
  }
  const data = {
    steam_username: $('acc-username').value,
    steam_password: $('acc-password').value,
    shared_secret: $('acc-shared').value,
    identity_secret: $('acc-identity').value,
  };
  const r = await fetch('/api/account/save', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  });
  const d = await r.json();
  showMsg('account-msg', d.msg, d.ok);
  if (d.ok) loadConfig();
}

function showMsg(id, msg, ok) {
  const el = $(id);
  el.textContent = msg;
  el.className = 'msg ' + (ok ? 'ok' : 'err');
  setTimeout(() => { el.textContent = ''; }, 4000);
}

function toast(msg) {
  const t = $('toast');
  t.textContent = msg;
  t.style.display = 'block';
  clearTimeout(t._timer);
  t._timer = setTimeout(() => { t.style.display = 'none'; }, 3000);
}

document.querySelectorAll('.tab-btn').forEach(btn => {
  btn.addEventListener('click', () => {
    document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
    document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
    btn.classList.add('active');
    $('tab-' + btn.dataset.tab).classList.add('active');
    if (btn.dataset.tab === 'dashboard') initLogs();
    if (btn.dataset.tab === 'config') { loadConfig(); loadConfigTable(); }
  });
});

$('btn-start').addEventListener('click', start);
$('btn-stop').addEventListener('click', stop);
$('btn-save-config').addEventListener('click', saveConfig);
$('btn-save-account').addEventListener('click', saveAccount);
$('btn-clear-log').addEventListener('click', () => { $('log-panel').textContent = ''; });

refreshStatus();
initLogs();
setInterval(refreshStatus, 3000);

// ==== 平台登录 ====
let shownInputPrompt = null;
let shownQrUrl = null;

const LOGIN_STATUS_TEXT = { idle: '未登录', running: '登录中…', success: '已登录', failed: '失败' };

async function refreshLoginStatus() {
  try {
    const r = await fetch('/api/login/status');
    const d = await r.json();
    ['steam', 'buff', 'uu'].forEach(p => {
      const s = (d[p] || { status: 'idle' }).status;
      const badge = $('login-' + p + '-badge');
      const msg = $('login-' + p + '-msg');
      if (badge) {
        badge.textContent = LOGIN_STATUS_TEXT[s] || '未登录';
        badge.className = 'badge ' + (s === 'success' || s === 'running' ? 'running' : 'stopped');
      }
      if (msg) msg.textContent = (d[p] || {}).msg || '';
    });
  } catch (e) { /* ignore */ }
}

async function startLogin(platform) {
  const r = await fetch('/api/login/start', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ platform }),
  });
  const d = await r.json();
  toast(d.msg);
  refreshLoginStatus();
}

async function pollInteract() {
  try {
    const r = await fetch('/api/login/interact');
    const d = await r.json();
    const req = d.request;
    const box = $('login-interact');
    if (!req) {
      shownInputPrompt = null;
      shownQrUrl = null;
      if (box) box.style.display = 'none';
      return;
    }
    if (req.type === 'input') {
      if (box) box.style.display = 'block';
      $('interact-qrcode').style.display = 'none';
      if (shownInputPrompt !== req.prompt) {
        shownInputPrompt = req.prompt;
        $('interact-input').style.display = 'block';
        $('interact-prompt').textContent = req.prompt || '请输入：';
        $('interact-value').value = '';
        $('interact-value').focus();
      }
    } else if (req.type === 'qrcode') {
      if (box) box.style.display = 'block';
      $('interact-input').style.display = 'none';
      $('interact-qrcode').style.display = 'block';
      if (shownQrUrl !== req.url) {
        shownQrUrl = req.url;
        const qr = await fetch('/api/login/qrcode?url=' + encodeURIComponent(req.url));
        const qd = await qr.json();
        if (qd.ok) $('interact-qr-img').src = qd.image;
      }
    }
  } catch (e) { /* ignore */ }
}

async function submitInteract() {
  const value = $('interact-value').value;
  await fetch('/api/login/respond', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ value }),
  });
  $('interact-value').value = '';
  shownInputPrompt = null;
}

document.querySelectorAll('[data-login]').forEach(btn => {
  btn.addEventListener('click', () => startLogin(btn.dataset.login));
});
$('interact-submit').addEventListener('click', submitInteract);
$('interact-value').addEventListener('keydown', e => { if (e.key === 'Enter') submitInteract(); });

setInterval(refreshLoginStatus, 2000);
setInterval(pollInteract, 1000);

// ==== 配置表格 ====
function fmtDefault(v) {
  if (v == null) return '';
  if (Array.isArray(v)) return JSON.stringify(v);
  return String(v);
}

function makeControl(f) {
  if (f.type === 'bool') {
    const sel = document.createElement('select');
    sel.className = 'cfg-input';
    const o1 = document.createElement('option');
    o1.value = 'true'; o1.textContent = 'true';
    const o2 = document.createElement('option');
    o2.value = 'false'; o2.textContent = 'false';
    sel.appendChild(o1);
    sel.appendChild(o2);
    sel.value = f.value ? 'true' : 'false';
    return sel;
  }
  const input = document.createElement('input');
  input.className = 'cfg-input';
  if (f.type === 'int' || f.type === 'float') {
    input.type = 'number';
    if (f.type === 'float') input.step = 'any';
    input.value = (f.value == null ? '' : f.value);
  } else if (f.type === 'array') {
    input.type = 'text';
    input.value = JSON.stringify(f.value);
    input.placeholder = '["A", "B"]';
  } else {
    input.type = 'text';
    input.value = (f.value == null ? '' : f.value);
  }
  return input;
}

function renderConfigTable(groups) {
  const container = $('config-table');
  container.innerHTML = '';
  (groups || []).forEach(g => {
    const groupDiv = document.createElement('div');
    groupDiv.className = 'config-group';
    const h3 = document.createElement('h3');
    h3.textContent = g.group;
    groupDiv.appendChild(h3);
    const table = document.createElement('table');
    table.className = 'cfg-table';
    const thead = document.createElement('thead');
    thead.innerHTML = '<tr><th>参数</th><th>当前值</th><th>默认值</th><th>可填值</th></tr>';
    table.appendChild(thead);
    const tbody = document.createElement('tbody');
    g.fields.forEach(f => {
      const tr = document.createElement('tr');
      tr.dataset.key = f.key;
      tr.dataset.type = f.type;
      const tdName = document.createElement('td');
      tdName.className = 'cfg-name';
      const label = document.createElement('div');
      label.className = 'cfg-label';
      label.textContent = f.label;
      const help = document.createElement('div');
      help.className = 'cfg-help';
      help.textContent = f.help || '';
      tdName.appendChild(label);
      tdName.appendChild(help);
      const tdVal = document.createElement('td');
      tdVal.appendChild(makeControl(f));
      const tdDef = document.createElement('td');
      tdDef.className = 'cfg-default';
      tdDef.textContent = fmtDefault(f.default);
      const tdOpt = document.createElement('td');
      tdOpt.className = 'cfg-options';
      tdOpt.textContent = f.options || '';
      tr.appendChild(tdName);
      tr.appendChild(tdVal);
      tr.appendChild(tdDef);
      tr.appendChild(tdOpt);
      tbody.appendChild(tr);
    });
    table.appendChild(tbody);
    groupDiv.appendChild(table);
    container.appendChild(groupDiv);
  });
}

async function loadConfigTable() {
  try {
    const r = await fetch('/api/config/table');
    const d = await r.json();
    renderConfigTable(d.groups);
  } catch (e) { /* ignore */ }
}

function collectTableValues() {
  const values = {};
  document.querySelectorAll('#config-table tbody tr').forEach(tr => {
    const key = tr.dataset.key;
    const type = tr.dataset.type;
    const control = tr.querySelector('.cfg-input');
    if (!control) return;
    if (type === 'bool') {
      values[key] = (control.value === 'true');
    } else {
      values[key] = control.value;
    }
  });
  return values;
}

async function saveTable() {
  const values = collectTableValues();
  const r = await fetch('/api/config/table/save', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ values }),
  });
  const d = await r.json();
  showMsg('config-table-msg', d.msg, d.ok);
  if (d.ok) loadConfigTable();
}

function showConfigView(mode) {
  $('config-table-view').style.display = (mode === 'table' ? 'block' : 'none');
  $('config-text-view').style.display = (mode === 'text' ? 'block' : 'none');
  $('btn-view-table').classList.toggle('active', mode === 'table');
  $('btn-view-text').classList.toggle('active', mode === 'text');
  if (mode === 'table') loadConfigTable();
  if (mode === 'text') loadConfig();
}

$('btn-view-table').addEventListener('click', () => showConfigView('table'));
$('btn-view-text').addEventListener('click', () => showConfigView('text'));
$('btn-save-table').addEventListener('click', saveTable);

// ==== 账号信息：表单 ↔ 文本实时同步（不写文件） ====
let accSyncing = false;

function accFormToText() {
  if (accSyncing) return;
  accSyncing = true;
  const acc = {
    shared_secret: $('acc-shared').value,
    identity_secret: $('acc-identity').value,
    steam_username: $('acc-username').value,
    steam_password: $('acc-password').value,
  };
  $('account-editor').value = JSON.stringify(acc, null, 2);
  accSyncing = false;
}

function accTextToForm() {
  if (accSyncing) return true;
  try {
    const acc = JSON.parse($('account-editor').value);
    accSyncing = true;
    $('acc-shared').value = acc.shared_secret || '';
    $('acc-identity').value = acc.identity_secret || '';
    $('acc-username').value = acc.steam_username || '';
    $('acc-password').value = acc.steam_password || '';
    accSyncing = false;
    $('account-editor').classList.remove('editor-error');
    return true;
  } catch (e) {
    $('account-editor').classList.add('editor-error');
    return false;
  }
}

function showAccView(mode) {
  $('acc-form-view').style.display = (mode === 'form' ? 'block' : 'none');
  $('acc-text-view').style.display = (mode === 'text' ? 'block' : 'none');
  $('btn-acc-form').classList.toggle('active', mode === 'form');
  $('btn-acc-text').classList.toggle('active', mode === 'text');
  if (mode === 'text') accFormToText();
}

async function resetAccount() {
  if (!confirm('确定恢复为默认值？当前账号信息将被清空。')) return;
  const r = await fetch('/api/account/reset', { method: 'POST' });
  const d = await r.json();
  showMsg('account-msg', d.msg, d.ok);
  if (d.ok) loadConfig();
}

async function importAccount() {
  const file = $('acc-import-file').files[0];
  if (!file) return;
  const content = await file.text();
  const r = await fetch('/api/account/import', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ content }),
  });
  const d = await r.json();
  showMsg('account-msg', d.msg, d.ok);
  if (d.ok) loadConfig();
  $('acc-import-file').value = '';
}

async function exportAccount() {
  const r = await fetch('/api/account/export');
  const d = await r.json();
  if (!d.ok) { showMsg('account-msg', '导出失败', false); return; }
  const blob = new Blob([d.content], { type: 'application/json' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = d.filename || 'steam_account_info.json5';
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
}

['acc-username', 'acc-password', 'acc-shared', 'acc-identity'].forEach(id => {
  $(id).addEventListener('input', accFormToText);
});
$('account-editor').addEventListener('input', accTextToForm);
$('btn-acc-form').addEventListener('click', () => showAccView('form'));
$('btn-acc-text').addEventListener('click', () => showAccView('text'));
$('btn-acc-reset').addEventListener('click', resetAccount);
$('btn-acc-import').addEventListener('click', () => $('acc-import-file').click());
$('acc-import-file').addEventListener('change', importAccount);
$('btn-acc-export').addEventListener('click', exportAccount);
